"""Lists server: the Lists page, and a JSON API over Home Assistant's to-do lists.

The app keeps no items. Each list is a `todo` entity in Home Assistant, and each change goes
through Home Assistant, so this page and the stock To-do page always show the same lists.
The app stores only the look of each list (icon, color, category, and whether the page hides it),
and the layout each person picked.

Related Documentation: DOCS.md.
"""

import json
import os
import re
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
LAYOUTS_FILE = DATA_DIR / "layouts.json"
INDEX_FILE = Path(__file__).parent / "static" / "index.html"
PORT = int(os.environ.get("PORT", 8099))
# Under the Supervisor, Core sees the app as the Supervisor user, which is an admin.
# That lets a non-admin person create and delete lists through this app.
HA_URL = os.environ.get("HA_URL", "http://supervisor/core/api")
HA_TOKEN = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HA_TOKEN", "")
HA_TIMEOUT = 10
ENTITY_PATTERN = re.compile(r"todo\.[a-z0-9_]+")
NAME_MAX = 40
ITEM_MAX = 200
ICON_MAX = 8  # characters: one emoji can be several code points
CATEGORY_MAX = 30
# Same names as the color classes on the page.
COLORS = ["teal", "blue", "indigo", "purple", "pink", "red", "orange", "amber", "green", "slate"]
DEFAULT_ICON = "📝"
LOOK_FIELDS = {"icon": str, "color": str, "category": str, "hidden": bool}
# The first one is the default.
LAYOUTS = ["tiles", "rows"]
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
    return {"icon": DEFAULT_ICON, "color": COLORS[zlib.crc32(entity_id.encode()) % len(COLORS)], "category": "",
            "hidden": False}


def validate_look(changes, current):
    """Return `current` with the user's `changes` applied."""
    if (not isinstance(changes, dict) or not set(changes) <= LOOK_FIELDS.keys()
            or not all(isinstance(value, LOOK_FIELDS[key]) for key, value in changes.items())):
        raise BadRequest("Invalid look.")
    look = {**current, **changes}
    look["icon"] = look["icon"].strip()
    look["category"] = " ".join(look["category"].split())
    if look["color"] not in COLORS or not 0 < len(look["icon"]) <= ICON_MAX:
        raise BadRequest("Invalid look.")
    if len(look["category"]) > CATEGORY_MAX:
        raise BadRequest(f"A category can have at most {CATEGORY_MAX} characters.")
    return look


def page_lists(todos, looks):
    """Return `todos` as the page shows them: with each list's look, sorted by name."""
    return sorted(({"entity_id": todo["entity_id"], "name": todo["name"], "open": todo["open"],
                    "deletable": todo["entry_id"] is not None,
                    **default_look(todo["entity_id"]), **looks.get(todo["entity_id"], {})} for todo in todos),
                  key=lambda todo: todo["name"].casefold())


def clean_name(name, todos):
    """Return the new list name `name`, trimmed. Reject blanks, long names, and names in use."""
    if not isinstance(name, str) or not name.strip():
        raise BadRequest("Enter a name.")
    name = " ".join(name.split())
    if len(name) > NAME_MAX:
        raise BadRequest(f"A name can have at most {NAME_MAX} characters.")
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
                    "/api/layout": self.get_layout})

    def do_PUT(self):
        self.route({"/api/look": self.put_look, "/api/items": self.put_item, "/api/layout": self.put_layout})

    def do_POST(self):
        self.route({"/api/lists": self.post_list, "/api/lists/delete": self.post_delete_list,
                    "/api/items": self.post_item, "/api/items/delete": self.post_delete_item,
                    "/api/items/clear": self.post_clear_items})

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

    def send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
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

    def get_lists(self, query):
        self.send_json(200, page_lists(fetch_lists(), load_looks()))

    def post_list(self, query):
        body = self.read_json()
        with save_lock:
            name = clean_name(body.get("name"), fetch_lists())
            changes = {key: body[key] for key in ("icon", "color", "category") if key in body}
            look = validate_look(changes, default_look("todo.new"))
            todo = create_list(name)
            looks = load_looks()
            looks[todo["entity_id"]] = look
            save_json(LOOKS_FILE, looks)
        self.send_json(200, page_lists([todo], looks)[0])

    def post_delete_list(self, query):
        entity_id = entity_param(self.read_json().get("entity_id"))
        with save_lock:
            todo = find_list(fetch_lists(), entity_id)
            if todo["entry_id"] is None:
                raise BadRequest(f"{todo['name']} comes from another integration. Remove it there.")
            ha("DELETE", f"config/config_entries/entry/{todo['entry_id']}")
            looks = load_looks()
            if looks.pop(entity_id, None):
                save_json(LOOKS_FILE, looks)
        self.send_json(200, {"deleted": entity_id})

    def put_look(self, query):
        body = self.read_json()
        entity_id = entity_param(body.pop("entity_id", None))
        with save_lock:
            looks = load_looks()
            looks[entity_id] = validate_look(body, {**default_look(entity_id), **looks.get(entity_id, {})})
            save_json(LOOKS_FILE, looks)
        self.send_json(200, looks[entity_id])

    def get_layout(self, query):
        self.send_json(200, {"layout": load_json(LAYOUTS_FILE, {}).get(self.user(), LAYOUTS[0])})

    def put_layout(self, query):
        layout = self.read_json().get("layout")
        if layout not in LAYOUTS:
            raise BadRequest("Unknown layout.")
        with save_lock:
            layouts = load_json(LAYOUTS_FILE, {})
            layouts[self.user()] = layout
            save_json(LAYOUTS_FILE, layouts)
        self.send_json(200, {"layout": layout})

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
