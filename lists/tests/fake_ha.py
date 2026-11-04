"""A fake Home Assistant REST API: the to-do lists, the Local To-do config flow, and the todo actions."""

import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

TOKEN = "test-token"


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class FakeHomeAssistant:
    """Serves the REST calls the app makes, on a free local port.

    `lists` maps entity IDs to {"name", "entry_id", "items"}. An `entry_id` of None marks a
    list of another integration. Tests change `lists` directly to act as the stock To-do page.
    """

    def __init__(self):
        self.lists = {}
        self.flows = set()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        self.url = f"http://127.0.0.1:{self.server.server_port}/api"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def add_list(self, name, local=True, items=()):
        entity_id = f"todo.{slugify(name)}"
        self.lists[entity_id] = {"name": name, "entry_id": uuid.uuid4().hex if local else None,
                                 "items": [{"uid": uuid.uuid4().hex, "summary": summary, "status": "needs_action"}
                                           for summary in items]}
        return entity_id

    def template(self):
        return json.dumps([{"entity_id": entity_id, "name": todo["name"], "entry_id": todo["entry_id"],
                            "state": str(sum(item["status"] == "needs_action" for item in todo["items"]))}
                           for entity_id, todo in self.lists.items()])

    def flow_step(self, flow_id, data):
        self.flows.remove(flow_id)
        entity_id = f"todo.{slugify(data['todo_list_name'])}"
        if entity_id in self.lists:
            return {"type": "abort", "reason": "already_configured"}
        self.add_list(data["todo_list_name"])
        return {"type": "create_entry", "result": {"entry_id": self.lists[entity_id]["entry_id"]}}

    def delete_entry(self, entry_id):
        for entity_id, todo in self.lists.items():
            if todo["entry_id"] == entry_id:
                del self.lists[entity_id]
                return {"require_restart": False}
        raise KeyError("Invalid entry specified")

    def todo_action(self, service, data, return_response):
        todo = self.lists.get(data.get("entity_id"))
        if not todo:
            raise KeyError("Unknown entity")
        matches = [item for item in todo["items"] if item["uid"] == data.get("item")]
        if service == "get_items":
            if not return_response:
                raise KeyError("Add ?return_response to query parameters.")
            return {"changed_states": [], "service_response": {data["entity_id"]: {"items": todo["items"]}}}
        if service == "add_item":
            todo["items"].append({"uid": uuid.uuid4().hex, "summary": data["item"], "status": "needs_action"})
        elif service in ("update_item", "remove_item") and not matches:
            raise KeyError(f"Item {data.get('item')} not found")
        elif service == "update_item":
            matches[0]["status"] = data["status"]
        elif service == "remove_item":
            todo["items"].remove(matches[0])
        elif service == "remove_completed_items":
            todo["items"] = [item for item in todo["items"] if item["status"] != "completed"]
        return []

    def handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.answer()

            def do_DELETE(self):
                self.answer()

            def log_message(self, format, *args):
                pass

            def answer(self):
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    return self.send(401, {"message": "Unauthorized"})
                url = urlsplit(self.path)
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
                try:
                    if url.path == "/api/template":
                        return self.send(200, fake.template(), "text/plain")
                    if url.path == "/api/config/config_entries/flow" and data.get("handler") == "local_todo":
                        flow_id = uuid.uuid4().hex
                        fake.flows.add(flow_id)
                        return self.send(200, {"flow_id": flow_id, "type": "form", "step_id": "user"})
                    if match := re.fullmatch(r"/api/config/config_entries/flow/(\w+)", url.path):
                        return self.send(200, fake.flow_step(match[1], data))
                    if match := re.fullmatch(r"/api/config/config_entries/entry/(\w+)", url.path):
                        return self.send(200, fake.delete_entry(match[1]))
                    if match := re.fullmatch(r"/api/services/todo/(\w+)", url.path):
                        return self.send(200, fake.todo_action(match[1], data, url.query == "return_response"))
                except KeyError as error:
                    return self.send(400, {"message": error.args[0]})
                self.send(404, {"message": "Not found"})

            def send(self, status, body, content_type="application/json"):
                text = (body if isinstance(body, str) else json.dumps(body)).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(text)))
                self.end_headers()
                self.wfile.write(text)

        return Handler
