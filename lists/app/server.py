"""Lists server: the Lists page, and a JSON API over Home Assistant's to-do lists.

The app keeps no items. Each list is a `todo` entity in Home Assistant, and each change goes
through Home Assistant, so this page and the stock To-do page always show the same lists.
The app stores only the look of each list (color, category, and whether the page hides it), the
categories, and each person's view settings.

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
# Same names as the color classes on the page. A custom color from the color wheel is a hex code instead.
COLORS = ["teal", "cyan", "blue", "navy", "indigo", "purple", "magenta", "pink", "red", "orange", "amber", "brown",
          "lime", "green", "slate", "graphite"]
CUSTOM_COLOR = re.compile(r"#[0-9a-f]{6}")
LOOK_FIELDS = {"color": str, "category": str, "hidden": bool}
# Each person's view settings. The first choice of each is the default.
SETTINGS = {"layout": ["tiles", "rows"], "order": ["added", "name"], "text": ["normal", "large"],
            "theme": ["device", "light", "dark"]}
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


class BadRequest(Exception):
    pass


class HomeAssistantError(Exception):
    pass


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
    return {"color": COLORS[zlib.crc32(entity_id.encode()) % len(COLORS)], "category": "", "hidden": False}


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


def load_categories():
    return load_json(CATEGORIES_FILE, [])


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


def save_looks(looks, categories):
    """Save `looks`, and every category that `categories` or `looks` name. Call under save_lock."""
    save_json(LOOKS_FILE, looks)
    save_json(CATEGORIES_FILE, all_categories(categories, looks))


def page_lists(todos, looks):
    """Return `todos` as the page shows them, with each list's look. The page sorts them."""
    return [{"entity_id": todo["entity_id"], "name": todo["name"], "open": todo["open"],
             "deletable": todo["entry_id"] is not None, **default_look(todo["entity_id"]),
             **looks.get(todo["entity_id"], {})} for todo in todos]


def load_settings(user):
    return {**{name: choices[0] for name, choices in SETTINGS.items()}, **load_json(SETTINGS_FILE, {}).get(user, {})}


def validate_settings(changes, current):
    """Return `current` with the person's `changes` applied."""
    if (not isinstance(changes, dict) or not set(changes) <= SETTINGS.keys()
            or not all(value in SETTINGS[name] for name, value in changes.items())):
        raise BadRequest("Invalid settings.")
    return {**current, **changes}


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
        raise HomeAssistantError("Cannot reach Home Assistant.")
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
    except (OSError, ValueError):
        raise HomeAssistantError("Cannot reach Home Assistant.")
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


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.route({"/": self.index, "/api/lists": self.get_lists, "/api/items": self.get_items,
                    "/api/settings": self.get_settings, **{route: self.get_vendor for route in VENDOR_ROUTES}})

    def do_HEAD(self):
        # The emoji picker asks for the ETag of its data with HEAD before it downloads the data again.
        self.route({route: self.get_vendor for route in VENDOR_ROUTES})

    def do_PUT(self):
        self.route({"/api/look": self.put_look, "/api/categories": self.put_categories, "/api/items": self.put_item,
                    "/api/settings": self.put_settings})

    def do_POST(self):
        self.route({"/api/lists": self.post_list, "/api/lists/delete": self.post_delete_list,
                    "/api/lists/rename": self.post_rename_list, "/api/categories/rename": self.post_rename_category, "/api/items": self.post_item,
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
            self.send_json(502, {"error": str(error)})

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
        self.send_json(200, {"lists": page_lists(fetch_lists(), looks),
                             "categories": all_categories(load_categories(), looks)})

    def post_list(self, query):
        body = self.read_json()
        with save_lock:
            name = clean_name(body.get("name"), fetch_lists())
            changes = {key: body[key] for key in ("color", "category") if key in body}
            look = validate_look(changes, default_look("todo.new"))
            todo = create_list(name)
            looks = load_looks()
            looks[todo["entity_id"]] = look
            save_looks(looks, load_categories())
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
            # The list's category stays, even when it was the last list in it.
            categories = all_categories(load_categories(), looks)
            looks.pop(entity_id, None)
            save_looks(looks, categories)
        self.send_json(200, {"deleted": entity_id})

    def put_look(self, query):
        body = self.read_json()
        entity_id = entity_param(body.pop("entity_id", None))
        with save_lock:
            looks = load_looks()
            # The list's old category stays, even when the list was the last one in it.
            categories = all_categories(load_categories(), looks)
            looks[entity_id] = validate_look(body, {**default_look(entity_id), **looks.get(entity_id, {})})
            save_looks(looks, categories)
        self.send_json(200, looks[entity_id])

    def put_categories(self, query):
        order = self.read_json().get("order")
        with save_lock:
            categories = reorder_categories(all_categories(load_categories(), load_looks()), order)
            save_json(CATEGORIES_FILE, categories)
        self.send_json(200, categories)

    def post_rename_category(self, query):
        body = self.read_json()
        with save_lock:
            looks = load_looks()
            save_looks(*rename_category(looks, all_categories(load_categories(), looks), body.get("from"),
                                        body.get("to")))
        self.send_json(200, {"renamed": body["from"]})

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
        uid, done = body.get("uid"), body.get("done")
        if not isinstance(uid, str) or not uid or not isinstance(done, bool):
            raise BadRequest("Expected uid and done.")
        self.send_json(200, todo_action("update_item", {"entity_id": entity_param(body.get("list")), "item": uid,
                                                        "status": "completed" if done else "needs_action"}))

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
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
