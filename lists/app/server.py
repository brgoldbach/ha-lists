"""Lists server: the Lists page, and a JSON API over Home Assistant's to-do lists.

The app keeps no items. Each list is a `todo` entity in Home Assistant, and each change goes
through Home Assistant, so this page and the stock To-do page always show the same lists.
The app stores only the look of each list (color, category, and whether the page hides it), the
categories, the order of the lists, and each person's favorites and view settings.

Related Documentation: DOCS.md.
"""

import base64
import json
import os
import re
import socket
import struct
import threading
import time
import traceback
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
LOOKS_FILE = DATA_DIR / "looks.json"
# A category stays after its last list leaves it, so the app keeps them apart from the looks.
CATEGORIES_FILE = DATA_DIR / "categories.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
# One order of the lists for everyone, and each person's favorites. Both hold entity IDs.
ORDER_FILE = DATA_DIR / "order.json"
FAVORITES_FILE = DATA_DIR / "favorites.json"
ORDER_MAX = 1000
STATIC_DIR = Path(__file__).parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"
CONTENT_TYPES = {".js": "text/javascript", ".json": "application/json"}
# Third-party files the page loads: the emoji picker and its emoji data.
VENDOR_ROUTES = {f"/{path.relative_to(STATIC_DIR)}": path for path in (STATIC_DIR / "vendor").rglob("*")
                 if path.suffix in CONTENT_TYPES}
PORT = int(os.environ.get("PORT", 8099))
# Under the Supervisor, Core sees the app as the Supervisor user, which is an admin.
# That lets a non-admin person create and delete lists through this app.
HA_URL = os.environ.get("HA_URL", "http://supervisor/core/api")
HA_TOKEN = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HA_TOKEN", "")
# Renaming an entity has no REST call, only a websocket command.
HA_WS_URL = os.environ.get("HA_WS_URL", "ws://supervisor/core/websocket")
HA_TIMEOUT = 10
ENTITY_PATTERN = re.compile(r"todo\.[a-z0-9_]+")
NAME_MAX = 40
ITEM_MAX = 200
CATEGORY_MAX = 30
EXPORT_VERSION = 1
SELF_CHECK_S = 3600
RETRY_S = 60
NOTIFICATION_ID = "lists_app"
# What the app uses from Home Assistant. The self-check reports any of them that an update takes away.
REQUIRED_ACTIONS = {"todo": {"add_item", "update_item", "remove_item", "remove_completed_items", "get_items"},
                    "persistent_notification": {"create", "dismiss"}}
# Same names as the color classes on the page. A custom color from the color wheel is a hex code instead.
COLORS = ["teal", "cyan", "blue", "navy", "indigo", "purple", "magenta", "pink", "red", "orange", "amber", "brown",
          "lime", "green", "slate", "graphite"]
CUSTOM_COLOR = re.compile(r"#[0-9a-f]{6}")
LOOK_FIELDS = {"color": str, "category": str, "hidden": bool}
# Each person's view settings. The first choice of each is the default.
SETTINGS = {"layout": ["tiles", "rows"], "density": ["comfortable", "compact"], "order": ["added", "name"],
            "favorites": ["show", "hide"], "text": ["normal", "large"], "theme": ["device", "light", "dark"]}
DEFAULT_SETTINGS = {name: choices[0] for name, choices in SETTINGS.items()}
# The to-do list of Home Assistant's Shopping list integration, which Home Assistant sets up at onboarding.
BUILT_IN_SHOPPING_LIST = "todo.shopping_list"
# A list is deletable here only if it is a Local To-do list: its config entry holds that one list.
LISTS_TEMPLATE = """
{%- set local = integration_entities('local_todo') -%}
{%- set found = namespace(lists=[]) -%}
{%- for todo in states.todo -%}
  {%- set found.lists = found.lists + [{
      "entity_id": todo.entity_id, "name": todo.name, "state": todo.state,
      "entry_id": config_entry_id(todo.entity_id) if todo.entity_id in local else none}] -%}
{%- endfor -%}
{{ found.lists | to_json }}
"""

# Under the Supervisor, only ingress may connect.
TRUSTED_CLIENT = "172.30.32.2" if "SUPERVISOR_TOKEN" in os.environ else None

# Held across each read-modify-write of a data file, and across creating or deleting a list.
save_lock = threading.Lock()
# What the last self-check found wrong, or "" when Home Assistant offers all the app uses.
problem = ""
# Set to run the self-check at once, for example after an unexpected error.
check_now = threading.Event()


class BadRequest(Exception):
    pass


class HomeAssistantError(Exception):
    pass


class Unreachable(HomeAssistantError):
    """Home Assistant did not answer, for example while it restarts. That alone is no sign that it changed."""


def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path, data):
    # Replace atomically so a power cut never leaves a half-written file.
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2))
    temp.replace(path)


def load_looks():
    return load_json(LOOKS_FILE, {})


def default_look(entity_id):
    # A list made on another page gets a stable color from its entity ID, so new tiles differ without setup.
    # Home Assistant's built-in Shopping list starts hidden: the store lists replace it (DOCS.md, "The built-in
    # Shopping list").
    return {"color": COLORS[zlib.crc32(entity_id.encode()) % len(COLORS)], "category": "",
            "hidden": entity_id == BUILT_IN_SHOPPING_LIST}


def validate_look(changes, current):
    """Return `current` with the user's `changes` applied."""
    if (not isinstance(changes, dict) or not set(changes) <= LOOK_FIELDS.keys()
            or not all(isinstance(value, LOOK_FIELDS[key]) for key, value in changes.items())):
        raise BadRequest("Invalid look.")
    look = {**current, **changes}
    look["category"] = clean_category(look["category"])
    look["color"] = look["color"].lower()
    if look["color"] not in COLORS and not CUSTOM_COLOR.fullmatch(look["color"]):
        raise BadRequest("Invalid look.")
    return look


def clean_category(category):
    if not isinstance(category, str):
        raise BadRequest("Invalid category.")
    category = " ".join(category.split())
    if len(category) > CATEGORY_MAX:
        raise BadRequest(f"A category can have at most {CATEGORY_MAX} characters.")
    return category


def load_categories(looks):
    """Return the saved categories in their order, then any other category that `looks` use."""
    return all_categories(load_json(CATEGORIES_FILE, []), looks)


def all_categories(categories, looks):
    """Return `categories` in their order, then the other categories that `looks` use.

    Each name shows once, whatever its case.
    """
    known = {}
    for name in [*categories, *(look.get("category", "") for look in looks.values())]:
        if name:
            known.setdefault(name.casefold(), name)
    return list(known.values())


def reorder_categories(categories, order):
    """Return `categories` in the order of `order`, which must name each of them once."""
    by_key = {name.casefold(): name for name in categories}
    if (not isinstance(order, list) or not all(isinstance(name, str) for name in order)
            or sorted(name.casefold() for name in order) != sorted(by_key)):
        raise BadRequest("The categories changed meanwhile. Try again.")
    return [by_key[name.casefold()] for name in order]


def rename_category(looks, categories, old, new):
    """Return `looks` and `categories` with category `old` renamed to `new`. A blank `new` removes the category."""
    if not isinstance(old, str) or not old.strip():
        raise BadRequest("Expected from and to.")
    key = " ".join(old.split()).casefold()
    new = clean_category(new)
    looks = {entity_id: {**look, "category": new} if look.get("category", "").casefold() == key else look
             for entity_id, look in looks.items()}
    # The new name takes the old one's place in the order.
    return looks, all_categories([new if name.casefold() == key else name for name in categories], looks)


def save_looks(looks, categories):
    """Save `looks`, and every category that `categories` or `looks` name. Call under save_lock.

    Pass the categories as they were before the change: a category stays after its last list leaves it.
    """
    save_json(LOOKS_FILE, looks)
    save_json(CATEGORIES_FILE, all_categories(categories, looks))


def page_lists(todos, looks):
    """Return `todos` as the page shows them, with each list's look. The page sorts them."""
    return [{"entity_id": todo["entity_id"], "name": todo["name"], "open": todo["open"],
             "deletable": todo["entry_id"] is not None, **default_look(todo["entity_id"]),
             **looks.get(todo["entity_id"], {})} for todo in todos]


def clean_order(order):
    """Return `order`, a list of entity IDs, without repeats."""
    if not isinstance(order, list) or len(order) > ORDER_MAX:
        raise BadRequest("Invalid order.")
    return list(dict.fromkeys(entity_param(entity_id) for entity_id in order))


def set_favorite(favorites, entity_id, favorite):
    """Return `favorites` with `entity_id` added at the end, or removed."""
    favorites = [other for other in favorites if other != entity_id]
    return [*favorites, entity_id] if favorite else favorites


def load_settings(user):
    return {**DEFAULT_SETTINGS, **load_json(SETTINGS_FILE, {}).get(user, {})}


def validate_settings(changes, current):
    """Return `current` with the person's `changes` applied."""
    if (not isinstance(changes, dict) or not set(changes) <= SETTINGS.keys()
            or not all(value in SETTINGS[name] for name, value in changes.items())):
        raise BadRequest("Invalid settings.")
    return {**current, **changes}


def clean_name(name, todos):
    """Return the list name `name`, trimmed. Reject blanks, long names, and names that `todos` use.

    A name needs a letter or a number: Home Assistant drops emoji from entity IDs, so "🛒" alone has none.
    """
    if not isinstance(name, str) or not name.strip():
        raise BadRequest("Enter a name.")
    name = " ".join(name.split())
    if len(name) > NAME_MAX:
        raise BadRequest(f"A name can have at most {NAME_MAX} characters.")
    if not any(character.isalnum() for character in name):
        raise BadRequest("A name needs a letter or a number.")
    if name.casefold() in {todo["name"].casefold() for todo in todos}:
        raise BadRequest(f"{name} already exists.")
    return name


def clean_summary(summary):
    if not isinstance(summary, str) or not summary.strip():
        raise BadRequest("Enter an item.")
    if len(summary.strip()) > ITEM_MAX:
        raise BadRequest(f"An item can have at most {ITEM_MAX} characters.")
    return summary.strip()


def entity_param(value):
    if not isinstance(value, str) or not ENTITY_PATTERN.fullmatch(value):
        raise BadRequest("Unknown list.")
    return value


def ha(method, path, body=None):
    """Call the Home Assistant REST API at `path`. Return the reply as JSON, or as text if it is not JSON."""
    request = Request(f"{HA_URL}/{path}", method=method,
                      data=None if body is None else json.dumps(body).encode(),
                      headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=HA_TIMEOUT) as response:
            text = response.read().decode()
            is_json = response.headers.get_content_type() == "application/json"
    except HTTPError as error:
        with error:
            try:
                message = json.loads(error.read())["message"]
            except (ValueError, KeyError, TypeError):
                message = error.reason
        raise HomeAssistantError(f"Home Assistant: {message}")
    except (URLError, OSError):
        raise Unreachable("Cannot reach Home Assistant.")
    return json.loads(text) if is_json else text


def send_frame(connection, message):
    # A client masks every frame (RFC 6455, section 5.3). Commands stay far below the 64 KiB of a 16-bit length.
    data = json.dumps(message).encode()
    mask = os.urandom(4)
    length = struct.pack("!B", 0x80 | len(data)) if len(data) < 126 else struct.pack("!BH", 0x80 | 126, len(data))
    connection.sendall(b"\x81" + length + mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(data)))


def receive_frame(stream):
    """Return the next text message from the server. Join fragments, and skip pings and pongs."""
    message = b""
    while True:
        first, second = stream.read(2)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", stream.read(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", stream.read(8))[0]
        payload = stream.read(length)
        opcode = first & 0x0F
        if opcode == 0x8:
            raise HomeAssistantError("Home Assistant closed the connection.")
        if opcode in (0x0, 0x1):
            message += payload
            if first & 0x80:
                return json.loads(message)


def ha_command(command):
    """Run one command on the Home Assistant websocket API. Return its result."""
    url = urlsplit(HA_WS_URL)
    try:
        with (socket.create_connection((url.hostname, url.port or 80), timeout=HA_TIMEOUT) as connection,
              connection.makefile("rb") as stream):
            key = base64.b64encode(os.urandom(16)).decode()
            connection.sendall(f"GET {url.path} HTTP/1.1\r\nHost: {url.netloc}\r\nUpgrade: websocket\r\n"
                               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
                               .encode())
            if b" 101 " not in stream.readline():
                raise HomeAssistantError("Home Assistant refused the websocket.")
            while stream.readline() not in (b"\r\n", b""):
                pass
            receive_frame(stream)  # auth_required
            send_frame(connection, {"type": "auth", "access_token": HA_TOKEN})
            if receive_frame(stream).get("type") != "auth_ok":
                raise HomeAssistantError("Home Assistant refused the app's token.")
            send_frame(connection, {"id": 1, **command})
            reply = receive_frame(stream)
    except OSError:
        raise Unreachable("Cannot reach Home Assistant.")
    except ValueError:
        raise HomeAssistantError("Home Assistant sent a websocket reply that the app cannot read.")
    if not reply.get("success"):
        raise HomeAssistantError(f"Home Assistant: {reply.get('error', {}).get('message', 'the command failed')}")
    return reply["result"]


def fetch_lists():
    """Return each to-do list as {"entity_id", "name", "open", "entry_id"}.

    `open` is the number of open items, or None while the list is unavailable.
    `entry_id` is the Local To-do config entry, or None for a list of another integration.
    """
    return [{"entity_id": todo["entity_id"], "name": todo["name"],
             "open": int(todo["state"]) if todo["state"].isdigit() else None, "entry_id": todo["entry_id"]}
            for todo in json.loads(ha("POST", "template", {"template": LISTS_TEMPLATE}))]


def find_list(todos, entity_id):
    for todo in todos:
        if todo["entity_id"] == entity_id:
            return todo
    raise BadRequest("That list no longer exists.")


def create_list(name):
    """Create a Local To-do list named `name`. Return it once its entity exists."""
    flow = ha("POST", "config/config_entries/flow", {"handler": "local_todo"})
    result = ha("POST", f"config/config_entries/flow/{flow['flow_id']}", {"todo_list_name": name})
    if result.get("reason") == "already_configured":
        # Home Assistant compares names without emoji and punctuation, so "🛒 Grocery" clashes with "Grocery".
        raise BadRequest("A list with nearly the same name already exists.")
    if result.get("type") != "create_entry":
        raise BadRequest(f"Home Assistant did not create the list ({result.get('reason', result.get('type'))}).")
    entry_id = result["result"]["entry_id"]
    # Home Assistant sets up the entry, and so adds the entity, just after the flow ends.
    for _ in range(20):
        for todo in fetch_lists():
            if todo["entry_id"] == entry_id:
                return todo
        time.sleep(0.25)
    raise HomeAssistantError(f"Home Assistant created {name}, but the list did not appear. Reload the page.")


def fetch_items(entity_id):
    reply = ha("POST", "services/todo/get_items?return_response", {"entity_id": entity_id})
    return reply["service_response"][entity_id]["items"]


def todo_action(service, data):
    """Run the `todo.<service>` action with `data`. Return the list's items after it."""
    ha("POST", f"services/todo/{service}", data)
    return fetch_items(data["entity_id"])


def export_data():
    """Return every list with its look and items, the categories and lists in order, and each person's favorites
    and settings.

    The order and the favorites name each list by its name: entity IDs can differ in another Home Assistant.
    """
    looks = load_looks()
    todos = page_lists(fetch_lists(), looks)
    names = {todo["entity_id"]: todo["name"] for todo in todos}

    def named(entity_ids):
        return [names[entity_id] for entity_id in entity_ids if entity_id in names]

    return {"version": EXPORT_VERSION, "categories": load_categories(looks), "order": named(load_json(ORDER_FILE, [])),
            "lists": [{"name": todo["name"], **{key: todo[key] for key in LOOK_FIELDS},
                       "items": [{key: item[key] for key in ("summary", "status", "description") if item.get(key)}
                                 for item in fetch_items(todo["entity_id"])]}
                      for todo in todos],
            "favorites": {user: named(entity_ids) for user, entity_ids in load_json(FAVORITES_FILE, {}).items()},
            "settings": load_json(SETTINGS_FILE, {})}


def name_list(value):
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise BadRequest("Expected a file exported from Lists.")
    return value


def clean_import(data):
    """Return `data`, a file from export_data, checked and trimmed."""
    if not isinstance(data, dict) or data.get("version") != EXPORT_VERSION or not isinstance(data.get("lists"), list):
        raise BadRequest("Expected a file exported from Lists.")
    lists = []
    for entry in data["lists"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("items"), list):
            raise BadRequest("Expected a file exported from Lists.")
        items = []
        for item in entry["items"]:
            if (not isinstance(item, dict) or item.get("status") not in ("needs_action", "completed")
                    or not isinstance(item.get("description", ""), str)):
                raise BadRequest(f"An item of {entry.get('name')} is not valid.")
            items.append({"summary": clean_summary(item.get("summary")), "status": item["status"],
                          "description": item.get("description", "")})
        lists.append({"name": clean_name(entry.get("name"), []), "items": items,
                      "look": validate_look({key: entry[key] for key in LOOK_FIELDS if key in entry},
                                            default_look("todo.import"))})
    favorites, settings = data.get("favorites", {}), data.get("settings", {})
    if not isinstance(favorites, dict) or not isinstance(settings, dict):
        raise BadRequest("Expected a file exported from Lists.")
    return {"lists": lists, "categories": [clean_category(name) for name in name_list(data.get("categories", []))],
            "order": name_list(data.get("order", [])),
            "favorites": {str(user): name_list(names) for user, names in favorites.items()},
            "settings": {str(user): validate_settings(choices, DEFAULT_SETTINGS) for user, choices in settings.items()}}


def import_data(data):
    """Add the lists and items of `data` that Home Assistant lacks. Take the looks, the orders, the favorites, and
    the settings.

    Delete nothing. Return the number of new lists and new items, and the names of the lists that Home Assistant
    did not create. Call under save_lock.
    """
    by_name = {todo["name"].casefold(): todo for todo in fetch_lists()}
    looks = load_looks()
    known = load_categories(looks)
    counts = {"lists": 0, "items": 0, "skipped": []}
    for entry in data["lists"]:
        todo = by_name.get(entry["name"].casefold())
        if not todo:
            try:
                todo = by_name[entry["name"].casefold()] = create_list(entry["name"])
            except BadRequest:
                # For example, a name too close to one that Home Assistant has. The other lists still come in.
                counts["skipped"].append(entry["name"])
                continue
            counts["lists"] += 1
        entity_id = todo["entity_id"]
        looks[entity_id] = entry["look"]
        have = {item["summary"].casefold() for item in fetch_items(entity_id)}
        for item in entry["items"]:
            if item["summary"].casefold() in have:
                continue
            # Only a Local To-do list takes a description; the Shopping list, for example, refuses one.
            described = {"description": item["description"]} if item["description"] and todo["entry_id"] else {}
            ha("POST", "services/todo/add_item", {"entity_id": entity_id, "item": item["summary"], **described})
            if item["status"] == "completed":
                # todo.add_item sets no status. todo.update_item finds the new item by its summary.
                ha("POST", "services/todo/update_item",
                   {"entity_id": entity_id, "item": item["summary"], "status": "completed"})
            have.add(item["summary"].casefold())
            counts["items"] += 1

    def entity_ids(names):
        return [by_name[name.casefold()]["entity_id"] for name in names if name.casefold() in by_name]

    # The file's orders come first, then what only this Home Assistant has.
    save_looks(looks, [*data["categories"], *known])
    save_json(ORDER_FILE, list(dict.fromkeys([*entity_ids(data["order"]), *load_json(ORDER_FILE, [])])))
    save_json(FAVORITES_FILE, {**load_json(FAVORITES_FILE, {}),
                               **{user: entity_ids(names) for user, names in data["favorites"].items()}})
    save_json(SETTINGS_FILE, {**load_json(SETTINGS_FILE, {}), **data["settings"]})
    return counts


def find_problem():
    """Return what the app needs and does not find, or "" when all is there.

    Raise Unreachable when Home Assistant does not answer.
    """
    for path in (LOOKS_FILE, CATEGORIES_FILE, ORDER_FILE, FAVORITES_FILE, SETTINGS_FILE):
        try:
            load_json(path, None)
        except ValueError:
            return f"its file {path.name} is damaged. Restore the app from a backup, or import an export"
    try:
        services = {entry["domain"]: set(entry["services"]) for entry in ha("GET", "services")}
        missing = sorted(f"{domain}.{action}" for domain, actions in REQUIRED_ACTIONS.items()
                         for action in actions - services.get(domain, set()))
        if missing:
            return f"Home Assistant no longer offers {', '.join(missing)}"
        if "local_todo" not in ha("GET", "config/config_entries/flow_handlers"):
            return "Home Assistant can no longer create Local To-do lists"
        fetch_lists()
        ha_command({"type": "get_config"})
    except Unreachable:
        raise
    except Exception as error:
        # Any other failure here means a call that used to work does not anymore.
        return f"a call to Home Assistant failed ({error!r})"
    return ""


def report(found):
    """Show `found` as a Home Assistant notification, or take the notification away when `found` is ""."""
    if found:
        ha("POST", "services/persistent_notification/create", {
            "notification_id": NOTIFICATION_ID, "title": "Lists app needs an update",
            "message": f"The Lists app found a problem: {found}.\n\nYour lists are safe in Home Assistant, and the app "
                       "keeps its settings. Look for an update of the Lists app."})
    else:
        ha("POST", "services/persistent_notification/dismiss", {"notification_id": NOTIFICATION_ID})


def self_check_loop():
    """Check Home Assistant at start, every hour, and after an unexpected error. Report each change in the result."""
    global problem
    reported = None
    while True:
        check_now.clear()
        try:
            problem = find_problem()
        except Unreachable:
            check_now.wait(RETRY_S)
            continue
        if problem != reported:
            print(f"Self-check: {problem or 'Home Assistant offers all that the app uses.'}", flush=True)
            try:
                report(problem)
                reported = problem
            except HomeAssistantError as error:
                print(f"Self-check: cannot show a notification ({error})", flush=True)
        check_now.wait(SELF_CHECK_S)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.route({"/": self.index, "/api/lists": self.get_lists, "/api/items": self.get_items,
                    "/api/settings": self.get_settings, "/api/export": self.get_export,
                    **{route: self.get_vendor for route in VENDOR_ROUTES}})

    def do_HEAD(self):
        # The emoji picker asks for the ETag of its data with HEAD before it downloads the data again.
        self.route({route: self.get_vendor for route in VENDOR_ROUTES})

    def do_PUT(self):
        self.route({"/api/look": self.put_look, "/api/categories": self.put_categories, "/api/order": self.put_order,
                    "/api/favorites": self.put_favorites, "/api/items": self.put_item,
                    "/api/settings": self.put_settings})

    def do_POST(self):
        self.route({"/api/lists": self.post_list, "/api/lists/delete": self.post_delete_list,
                    "/api/lists/rename": self.post_rename_list, "/api/categories/rename": self.post_rename_category,
                    "/api/import": self.post_import, "/api/items": self.post_item,
                    "/api/items/delete": self.post_delete_item, "/api/items/clear": self.post_clear_items})

    def route(self, routes):
        if TRUSTED_CLIENT and self.client_address[0] != TRUSTED_CLIENT:
            return self.send_json(403, {"error": "Forbidden."})
        url = urlsplit(self.path)
        handler = routes.get(url.path)
        if not handler:
            return self.send_json(404, {"error": "Not found."})
        try:
            handler(parse_qs(url.query))
        except BadRequest as error:
            self.send_json(400, {"error": str(error)})
        except HomeAssistantError as error:
            if not isinstance(error, Unreachable):
                check_now.set()
            self.send_json(502, {"error": str(error)})
        except Exception:
            # Most likely Home Assistant changed a reply. Log it, check Home Assistant at once, and keep serving.
            traceback.print_exc()
            check_now.set()
            self.send_json(500, {"error": "Lists hit a problem that it cannot handle yet. Its log has the details."})

    def log_message(self, format, *args):
        # The page polls every few seconds, which would fill the app log.
        pass

    def send(self, status, content_type, body, headers={}):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, status, data):
        self.send(status, "application/json", json.dumps(data).encode())

    def read_json(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise BadRequest("Body is not JSON.")
        if not isinstance(data, dict):
            raise BadRequest("Body is not a JSON object.")
        return data

    def user(self):
        # Ingress names the Home Assistant user who views the page. Without the Supervisor, all share one entry.
        return self.headers.get("X-Remote-User-Id", "")

    def index(self, query):
        self.send(200, "text/html; charset=utf-8", INDEX_FILE.read_bytes())

    def get_vendor(self, query):
        path = VENDOR_ROUTES[urlsplit(self.path).path]
        body = path.read_bytes()
        self.send(200, CONTENT_TYPES[path.suffix], body,
                  {"ETag": f'"{zlib.crc32(body):08x}"', "Cache-Control": "max-age=86400"})

    def get_lists(self, query):
        looks = load_looks()
        self.send_json(200, {"lists": page_lists(fetch_lists(), looks), "categories": load_categories(looks),
                             "order": load_json(ORDER_FILE, []),
                             "favorites": load_json(FAVORITES_FILE, {}).get(self.user(), []), "problem": problem})

    def post_list(self, query):
        body = self.read_json()
        with save_lock:
            name = clean_name(body.get("name"), fetch_lists())
            changes = {key: body[key] for key in ("color", "category") if key in body}
            look = validate_look(changes, default_look("todo.new"))
            todo = create_list(name)
            looks = load_looks()
            categories = load_categories(looks)
            looks[todo["entity_id"]] = look
            save_looks(looks, categories)
        self.send_json(200, page_lists([todo], looks)[0])

    def post_rename_list(self, query):
        body = self.read_json()
        entity_id = entity_param(body.get("entity_id"))
        todos = fetch_lists()
        find_list(todos, entity_id)
        name = clean_name(body.get("name"), [todo for todo in todos if todo["entity_id"] != entity_id])
        # The same change as renaming the entity in Home Assistant's settings. The entity ID stays.
        ha_command({"type": "config/entity_registry/update", "entity_id": entity_id, "name": name})
        self.send_json(200, {"entity_id": entity_id, "name": name})

    def post_delete_list(self, query):
        entity_id = entity_param(self.read_json().get("entity_id"))
        with save_lock:
            todo = find_list(fetch_lists(), entity_id)
            if todo["entry_id"] is None:
                raise BadRequest(f"{todo['name']} comes from another integration. Remove it there.")
            ha("DELETE", f"config/config_entries/entry/{todo['entry_id']}")
            looks = load_looks()
            categories = load_categories(looks)
            looks.pop(entity_id, None)
            save_looks(looks, categories)
            save_json(ORDER_FILE, [other for other in load_json(ORDER_FILE, []) if other != entity_id])
            save_json(FAVORITES_FILE, {user: set_favorite(entity_ids, entity_id, False)
                                       for user, entity_ids in load_json(FAVORITES_FILE, {}).items()})
        self.send_json(200, {"deleted": entity_id})

    def put_look(self, query):
        body = self.read_json()
        entity_id = entity_param(body.pop("entity_id", None))
        with save_lock:
            looks = load_looks()
            categories = load_categories(looks)
            looks[entity_id] = validate_look(body, {**default_look(entity_id), **looks.get(entity_id, {})})
            save_looks(looks, categories)
        self.send_json(200, looks[entity_id])

    def put_categories(self, query):
        order = self.read_json().get("order")
        with save_lock:
            categories = reorder_categories(load_categories(load_looks()), order)
            save_json(CATEGORIES_FILE, categories)
        self.send_json(200, categories)

    def put_order(self, query):
        order = clean_order(self.read_json().get("order"))
        with save_lock:
            save_json(ORDER_FILE, order)
        self.send_json(200, order)

    def put_favorites(self, query):
        body = self.read_json()
        entity_id, favorite = entity_param(body.get("entity_id")), body.get("favorite")
        if not isinstance(favorite, bool):
            raise BadRequest("Expected entity_id and favorite.")
        with save_lock:
            everyone = load_json(FAVORITES_FILE, {})
            everyone[self.user()] = set_favorite(everyone.get(self.user(), []), entity_id, favorite)
            save_json(FAVORITES_FILE, everyone)
        self.send_json(200, everyone[self.user()])

    def post_rename_category(self, query):
        body = self.read_json()
        with save_lock:
            looks = load_looks()
            looks, categories = rename_category(looks, load_categories(looks), body.get("from"), body.get("to"))
            save_looks(looks, categories)
        self.send_json(200, categories)

    def get_export(self, query):
        body = json.dumps(export_data(), indent=2, ensure_ascii=False).encode()
        self.send(200, "application/json; charset=utf-8", body,
                  {"Content-Disposition": 'attachment; filename="lists.json"'})

    def post_import(self, query):
        data = clean_import(self.read_json())
        with save_lock:
            counts = import_data(data)
        self.send_json(200, counts)

    def get_settings(self, query):
        self.send_json(200, load_settings(self.user()))

    def put_settings(self, query):
        body = self.read_json()
        with save_lock:
            settings = validate_settings(body, load_settings(self.user()))
            everyone = load_json(SETTINGS_FILE, {})
            everyone[self.user()] = settings
            save_json(SETTINGS_FILE, everyone)
        self.send_json(200, settings)

    def get_items(self, query):
        self.send_json(200, fetch_items(entity_param(query.get("list", [None])[0])))

    def post_item(self, query):
        body = self.read_json()
        self.send_json(200, todo_action("add_item", {"entity_id": entity_param(body.get("list")),
                                                     "item": clean_summary(body.get("summary"))}))

    def put_item(self, query):
        body = self.read_json()
        uid = body.get("uid")
        changes = {}
        if isinstance(body.get("done"), bool):
            changes["status"] = "completed" if body["done"] else "needs_action"
        if "summary" in body:
            changes["rename"] = clean_summary(body["summary"])
        if not isinstance(uid, str) or not uid or not changes:
            raise BadRequest("Expected uid, and done or summary.")
        self.send_json(200, todo_action("update_item", {"entity_id": entity_param(body.get("list")), "item": uid,
                                                        **changes}))

    def post_delete_item(self, query):
        body = self.read_json()
        uid = body.get("uid")
        if not isinstance(uid, str) or not uid:
            raise BadRequest("Expected uid.")
        self.send_json(200, todo_action("remove_item", {"entity_id": entity_param(body.get("list")), "item": uid}))

    def post_clear_items(self, query):
        self.send_json(200, todo_action("remove_completed_items",
                                        {"entity_id": entity_param(self.read_json().get("list"))}))


if __name__ == "__main__":
    threading.Thread(target=self_check_loop, daemon=True).start()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
