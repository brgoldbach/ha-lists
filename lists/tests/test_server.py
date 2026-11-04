import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

import server  # noqa: E402
from fake_ha import TOKEN, FakeHomeAssistant  # noqa: E402
from server import BadRequest, clean_name, default_look, page_lists, validate_look  # noqa: E402

LOOK = {"icon": "🛒", "color": "teal", "category": "", "hidden": False}


def todo(name, entry_id="entry", open_items=0):
    return {"entity_id": f"todo.{name.lower()}", "name": name, "open": open_items, "entry_id": entry_id}


class ValidateLookTest(unittest.TestCase):
    def test_applies_changes(self):
        self.assertEqual(validate_look({"icon": " 🔨 ", "category": "  Home   stores ", "hidden": True}, LOOK),
                         {"icon": "🔨", "color": "teal", "category": "Home stores", "hidden": True})

    def test_rejects_unknown_color(self):
        with self.assertRaises(BadRequest):
            validate_look({"color": "beige"}, LOOK)

    def test_rejects_unknown_field_and_wrong_type(self):
        for changes in ({"size": 3}, {"hidden": "yes"}, {"icon": ""}, {"icon": "x" * 9}, {"category": None},
                        {"category": "x" * 31}, ["icon"]):
            with self.subTest(changes=changes), self.assertRaises(BadRequest):
                validate_look(changes, LOOK)


class CleanNameTest(unittest.TestCase):
    def test_trims_and_joins_spaces(self):
        self.assertEqual(clean_name("  Harbor   Freight ", []), "Harbor Freight")

    def test_rejects_blank_long_and_used_names(self):
        for name in ("", "   ", None, "x" * 41, "grocery"):
            with self.subTest(name=name), self.assertRaises(BadRequest):
                clean_name(name, [todo("Grocery")])


class PageListsTest(unittest.TestCase):
    def test_sorts_by_name_and_marks_deletable(self):
        lists = page_lists([todo("Lowes"), todo("Shopping", entry_id=None), todo("grocery")], {})
        self.assertEqual([item["name"] for item in lists], ["grocery", "Lowes", "Shopping"])
        self.assertEqual([item["deletable"] for item in lists], [True, True, False])
        self.assertNotIn("entry_id", lists[0])

    def test_default_look_is_stable_and_stored_look_wins(self):
        self.assertEqual(default_look("todo.lowes"), default_look("todo.lowes"))
        [item] = page_lists([todo("Lowes")], {"todo.lowes": {"icon": "🔨", "color": "blue"}})
        self.assertEqual((item["icon"], item["color"], item["category"], item["hidden"]), ("🔨", "blue", "", False))


class AppTest(unittest.TestCase):
    """The app's API end to end, against a fake Home Assistant."""

    def setUp(self):
        self.ha = FakeHomeAssistant()
        self.addCleanup(self.ha.close)
        data = tempfile.TemporaryDirectory()
        self.addCleanup(data.cleanup)
        for name, value in (("HA_URL", self.ha.url), ("HA_TOKEN", TOKEN),
                            ("LOOKS_FILE", Path(data.name) / "looks.json"),
                            ("LAYOUTS_FILE", Path(data.name) / "layouts.json")):
            patcher = mock.patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        app = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=app.serve_forever, daemon=True).start()
        self.addCleanup(app.server_close)
        self.addCleanup(app.shutdown)
        self.url = f"http://127.0.0.1:{app.server_port}"

    def call(self, method, path, body=None, user=None):
        request = Request(self.url + path, method=method, data=None if body is None else json.dumps(body).encode(),
                          headers={"X-Remote-User-Id": user} if user else {})
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def lists(self):
        return {item["entity_id"]: item for item in self.call("GET", "/api/lists")[1]}

    def test_shows_every_todo_list(self):
        self.ha.add_list("Shopping list", local=False, items=["Milk"])
        self.ha.add_list("Grocery", items=["Eggs", "Bread"])
        lists = self.lists()
        self.assertEqual({entity_id: (item["name"], item["open"], item["deletable"]) for entity_id, item in lists.items()},
                         {"todo.shopping_list": ("Shopping list", 1, False), "todo.grocery": ("Grocery", 2, True)})

    def test_creates_local_todo_list_with_its_look(self):
        status, created = self.call("POST", "/api/lists", {"name": " Harbor  Freight ", "icon": "🔧", "color": "red",
                                                           "category": "Stores"})
        self.assertEqual(status, 200)
        self.assertEqual(created["entity_id"], "todo.harbor_freight")
        self.assertEqual(self.ha.lists["todo.harbor_freight"]["name"], "Harbor Freight")
        self.assertEqual((created["icon"], created["color"], created["category"]), ("🔧", "red", "Stores"))
        self.assertEqual(self.lists()["todo.harbor_freight"]["icon"], "🔧")

    def test_create_rejects_used_name_and_bad_look(self):
        self.ha.add_list("Grocery")
        self.assertEqual(self.call("POST", "/api/lists", {"name": "grocery"})[0], 400)
        self.assertEqual(self.call("POST", "/api/lists", {"name": "Lowes", "color": "beige"})[0], 400)
        self.assertEqual(list(self.ha.lists), ["todo.grocery"])

    def test_item_changes_reach_home_assistant(self):
        entity_id = self.ha.add_list("Grocery")
        status, items = self.call("POST", "/api/items", {"list": entity_id, "summary": " Eggs "})
        self.assertEqual((status, [item["summary"] for item in items]), (200, ["Eggs"]))
        uid = items[0]["uid"]
        items = self.call("PUT", "/api/items", {"list": entity_id, "uid": uid, "done": True})[1]
        self.assertEqual(items[0]["status"], "completed")
        self.assertEqual(self.lists()[entity_id]["open"], 0)
        self.assertEqual(self.call("POST", "/api/items/clear", {"list": entity_id})[1], [])
        self.assertEqual(self.ha.lists[entity_id]["items"], [])

    def test_removes_item(self):
        entity_id = self.ha.add_list("Grocery", items=["Eggs", "Bread"])
        uid = self.ha.lists[entity_id]["items"][0]["uid"]
        items = self.call("POST", "/api/items/delete", {"list": entity_id, "uid": uid})[1]
        self.assertEqual([item["summary"] for item in items], ["Bread"])

    def test_changes_made_in_home_assistant_show_on_page(self):
        entity_id = self.ha.add_list("Grocery")
        self.ha.lists[entity_id]["items"].append({"uid": "1", "summary": "Coffee", "status": "needs_action"})
        self.ha.add_list("Lowes")
        self.assertEqual(self.call("GET", f"/api/items?list={entity_id}")[1][0]["summary"], "Coffee")
        self.assertIn("todo.lowes", self.lists())

    def test_deletes_local_todo_list_and_its_look(self):
        entity_id = self.call("POST", "/api/lists", {"name": "Lowes", "icon": "🔨", "color": "blue"})[1]["entity_id"]
        self.assertEqual(self.call("POST", "/api/lists/delete", {"entity_id": entity_id})[0], 200)
        self.assertNotIn(entity_id, self.ha.lists)
        self.assertEqual(json.loads(server.LOOKS_FILE.read_text()), {})

    def test_does_not_delete_list_of_other_integration(self):
        entity_id = self.ha.add_list("Shopping list", local=False)
        self.assertEqual(self.call("POST", "/api/lists/delete", {"entity_id": entity_id})[0], 400)
        self.assertIn(entity_id, self.ha.lists)

    def test_hides_list(self):
        entity_id = self.ha.add_list("Long Term Projects")
        status, look = self.call("PUT", "/api/look", {"entity_id": entity_id, "hidden": True})
        self.assertEqual((status, look["hidden"]), (200, True))
        self.assertTrue(self.lists()[entity_id]["hidden"])

    def test_moves_list_to_category(self):
        entity_id = self.ha.add_list("Long Term Projects")
        self.call("PUT", "/api/look", {"entity_id": entity_id, "category": "Projects"})
        self.assertEqual(self.lists()[entity_id]["category"], "Projects")

    def test_keeps_a_layout_for_each_person(self):
        self.assertEqual(self.call("GET", "/api/layout", user="a")[1], {"layout": "tiles"})
        self.assertEqual(self.call("PUT", "/api/layout", {"layout": "rows"}, user="a"), (200, {"layout": "rows"}))
        self.assertEqual(self.call("GET", "/api/layout", user="a")[1], {"layout": "rows"})
        self.assertEqual(self.call("GET", "/api/layout", user="b")[1], {"layout": "tiles"})
        self.assertEqual(self.call("PUT", "/api/layout", {"layout": "grid"}, user="a")[0], 400)

    def test_rejects_entity_that_is_not_a_todo_list(self):
        self.assertEqual(self.call("GET", "/api/items?list=light.kitchen")[0], 400)
        self.assertEqual(self.call("POST", "/api/items", {"list": "todo.grocery", "summary": " "})[0], 400)

    def test_reports_home_assistant_errors(self):
        status, body = self.call("PUT", "/api/items", {"list": "todo.gone", "uid": "1", "done": True})
        self.assertEqual((status, body["error"]), (502, "Home Assistant: Unknown entity"))
        self.ha.close()
        self.assertEqual(self.call("GET", "/api/lists"), (502, {"error": "Cannot reach Home Assistant."}))

    def test_only_ingress_may_connect_under_supervisor(self):
        with mock.patch.object(server, "TRUSTED_CLIENT", "172.30.32.2"):
            self.assertEqual(self.call("GET", "/api/lists")[0], 403)


if __name__ == "__main__":
    unittest.main()
