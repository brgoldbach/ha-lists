import io
import json
import struct
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
from server import (BadRequest, HomeAssistantError, Unreachable, all_categories, clean_name, clean_order,  # noqa: E402
                    default_look, page_lists, receive_frame, rename_category, reorder_categories, set_favorite,
                    validate_look, validate_settings)

LOOK = {"color": "teal", "category": "", "hidden": False}


def todo(name, entry_id="entry", open_items=0):
    return {"entity_id": f"todo.{name.lower()}", "name": name, "open": open_items, "entry_id": entry_id}


class ValidateLookTest(unittest.TestCase):
    def test_applies_changes(self):
        self.assertEqual(validate_look({"category": "  Home   stores ", "hidden": True}, LOOK),
                         {"color": "teal", "category": "Home stores", "hidden": True})

    def test_accepts_custom_color(self):
        self.assertEqual(validate_look({"color": "#3A86FF"}, LOOK)["color"], "#3a86ff")

    def test_rejects_unknown_color(self):
        for color in ("beige", "#3a86f", "#3a86ffaa", "3a86ff"):
            with self.subTest(color=color), self.assertRaises(BadRequest):
                validate_look({"color": color}, LOOK)

    def test_rejects_unknown_field_and_wrong_type(self):
        for changes in ({"icon": "🛒"}, {"hidden": "yes"}, {"category": None}, {"category": "x" * 31}, ["color"]):
            with self.subTest(changes=changes), self.assertRaises(BadRequest):
                validate_look(changes, LOOK)


class OrderAndFavoritesTest(unittest.TestCase):
    def test_order_drops_repeats(self):
        self.assertEqual(clean_order(["todo.b", "todo.a", "todo.b"]), ["todo.b", "todo.a"])

    def test_rejects_bad_order(self):
        for order in ("todo.a", ["light.kitchen"], [1], ["todo.a"] * 1001):
            with self.subTest(order=order[:2]), self.assertRaises(BadRequest):
                clean_order(order)

    def test_favorite_goes_last_and_comes_off(self):
        self.assertEqual(set_favorite(["todo.a", "todo.b"], "todo.a", True), ["todo.b", "todo.a"])
        self.assertEqual(set_favorite(["todo.a", "todo.b"], "todo.a", False), ["todo.b"])


class ValidateSettingsTest(unittest.TestCase):
    CURRENT = {"layout": "tiles", "order": "added", "favorites": "show", "text": "normal", "theme": "device"}

    def test_applies_changes(self):
        self.assertEqual(validate_settings({"layout": "rows", "theme": "dark"}, self.CURRENT),
                         {**self.CURRENT, "layout": "rows", "theme": "dark"})

    def test_rejects_unknown_setting_and_choice(self):
        for changes in ({"size": "large"}, {"theme": "blue"}, {"order": ["name"]}, ["layout"]):
            with self.subTest(changes=changes), self.assertRaises(BadRequest):
                validate_settings(changes, self.CURRENT)


class AllCategoriesTest(unittest.TestCase):
    def test_keeps_order_and_adds_used_categories_once_each(self):
        looks = {"todo.grocery": {**LOOK, "category": "stores"}, "todo.fence": {**LOOK, "category": "Projects"},
                 "todo.old": {"color": "teal"}}
        self.assertEqual(all_categories(["Stores", "Garden"], looks), ["Stores", "Garden", "Projects"])


class ReorderCategoriesTest(unittest.TestCase):
    def test_takes_new_order_with_stored_spelling(self):
        self.assertEqual(reorder_categories(["Projects", "Stores"], ["stores", "projects"]), ["Stores", "Projects"])

    def test_rejects_order_that_misses_or_adds_a_category(self):
        for order in (["Stores"], ["Stores", "Projects", "Garden"], ["Stores", "Stores"], "Stores", [1, 2]):
            with self.subTest(order=order), self.assertRaises(BadRequest):
                reorder_categories(["Projects", "Stores"], order)


class RenameCategoryTest(unittest.TestCase):
    LOOKS = {"todo.grocery": {**LOOK, "category": "Stores"}, "todo.lowes": {**LOOK, "category": "stores"},
             "todo.fence": {**LOOK, "category": "Projects"}, "todo.old": {"color": "teal"}}
    CATEGORIES = ["Garden", "Projects", "Stores"]

    def test_renames_in_every_list_whatever_the_case(self):
        looks, categories = rename_category(self.LOOKS, self.CATEGORIES, " STORES ", "Shops")
        self.assertEqual({entity_id: look.get("category") for entity_id, look in looks.items()},
                         {"todo.grocery": "Shops", "todo.lowes": "Shops", "todo.fence": "Projects", "todo.old": None})
        self.assertEqual(categories, ["Garden", "Projects", "Shops"])

    def test_renamed_category_keeps_its_place(self):
        self.assertEqual(rename_category(self.LOOKS, self.CATEGORIES, "Garden", "Yard")[1],
                         ["Yard", "Projects", "Stores"])

    def test_blank_name_removes_category(self):
        looks, categories = rename_category(self.LOOKS, self.CATEGORIES, "Projects", " ")
        self.assertEqual((looks["todo.fence"]["category"], categories), ("", ["Garden", "Stores"]))

    def test_rejects_bad_names(self):
        for old, new in (("", "Shops"), (None, "Shops"), ("Stores", None), ("Stores", "x" * 31)):
            with self.subTest(old=old, new=new), self.assertRaises(BadRequest):
                rename_category(self.LOOKS, self.CATEGORIES, old, new)


class CleanNameTest(unittest.TestCase):
    def test_trims_and_joins_spaces(self):
        self.assertEqual(clean_name("  Harbor   Freight ", []), "Harbor Freight")

    def test_keeps_emoji(self):
        self.assertEqual(clean_name(" 🛒  Grocery ", []), "🛒 Grocery")

    def test_rejects_blank_long_used_and_letterless_names(self):
        for name in ("", "   ", None, "x" * 41, "grocery", "🛒", "🛒 !"):
            with self.subTest(name=name), self.assertRaises(BadRequest):
                clean_name(name, [todo("Grocery")])


class PageListsTest(unittest.TestCase):
    def test_marks_deletable(self):
        lists = page_lists([todo("Lowes"), todo("Shopping", entry_id=None)], {})
        self.assertEqual([item["deletable"] for item in lists], [True, False])
        self.assertNotIn("entry_id", lists[0])

    def test_built_in_shopping_list_starts_hidden(self):
        self.assertEqual((default_look("todo.shopping_list")["hidden"], default_look("todo.grocery")["hidden"]),
                         (True, False))
        [item] = page_lists([todo("Shopping_list", entry_id=None)], {"todo.shopping_list": {"hidden": False}})
        self.assertFalse(item["hidden"])

    def test_default_look_is_stable_and_stored_look_wins(self):
        self.assertEqual(default_look("todo.lowes"), default_look("todo.lowes"))
        [item] = page_lists([todo("Lowes")], {"todo.lowes": {"color": "blue"}})
        self.assertEqual((item["color"], item["category"], item["hidden"]), ("blue", "", False))


def frame(opcode, payload, final=True):
    """Return one unmasked server frame."""
    length = bytes([len(payload)]) if len(payload) < 126 else bytes([126]) + struct.pack("!H", len(payload))
    return bytes([(0x80 if final else 0) | opcode]) + length + payload


class ReceiveFrameTest(unittest.TestCase):
    def test_joins_fragments_and_skips_pings(self):
        message = json.dumps({"id": 1, "result": list(range(60))}).encode()
        stream = io.BytesIO(frame(0x1, message[:10], final=False) + frame(0x9, b"") + frame(0x0, message[10:]))
        self.assertEqual(receive_frame(stream), json.loads(message))

    def test_reports_close(self):
        with self.assertRaises(HomeAssistantError):
            receive_frame(io.BytesIO(frame(0x8, b"")))


class AppTest(unittest.TestCase):
    """The app's API end to end, against a fake Home Assistant."""

    def setUp(self):
        self.ha = FakeHomeAssistant()
        self.addCleanup(self.ha.close)
        data = tempfile.TemporaryDirectory()
        self.addCleanup(data.cleanup)
        for name, value in (("HA_URL", self.ha.url), ("HA_WS_URL", self.ha.ws_url), ("HA_TOKEN", TOKEN),
                            ("LOOKS_FILE", Path(data.name) / "looks.json"),
                            ("CATEGORIES_FILE", Path(data.name) / "categories.json"),
                            ("SETTINGS_FILE", Path(data.name) / "settings.json"),
                            ("ORDER_FILE", Path(data.name) / "order.json"),
                            ("FAVORITES_FILE", Path(data.name) / "favorites.json")):
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
        return {item["entity_id"]: item for item in self.call("GET", "/api/lists")[1]["lists"]}

    def categories(self):
        return self.call("GET", "/api/lists")[1]["categories"]

    def test_shows_every_todo_list(self):
        self.ha.add_list("Shopping list", local=False, items=["Milk"])
        self.ha.add_list("Grocery", items=["Eggs", "Bread"])
        lists = self.lists()
        self.assertEqual({entity_id: (item["name"], item["open"], item["deletable"])
                          for entity_id, item in lists.items()},
                         {"todo.shopping_list": ("Shopping list", 1, False), "todo.grocery": ("Grocery", 2, True)})

    def test_creates_local_todo_list_with_its_look(self):
        status, created = self.call("POST", "/api/lists", {"name": " 🔧 Harbor  Freight ", "color": "red",
                                                           "category": "Stores"})
        self.assertEqual(status, 200)
        self.assertEqual(created["entity_id"], "todo.harbor_freight")
        self.assertEqual(self.ha.lists["todo.harbor_freight"]["name"], "🔧 Harbor Freight")
        self.assertEqual((created["name"], created["color"], created["category"]),
                         ("🔧 Harbor Freight", "red", "Stores"))
        self.assertEqual(self.lists()["todo.harbor_freight"]["color"], "red")

    def test_create_reports_name_that_home_assistant_finds_too_close(self):
        self.ha.add_list("Grocery")
        status, body = self.call("POST", "/api/lists", {"name": "🛒 Grocery"})
        self.assertEqual((status, body["error"]), (400, "A list with nearly the same name already exists."))

    def test_renames_list_in_home_assistant(self):
        entity_id = self.ha.add_list("Grocery")
        self.ha.add_list("Lowes")
        self.assertEqual(self.call("POST", "/api/lists/rename", {"entity_id": entity_id, "name": " 🛒 Grocery "}),
                         (200, {"entity_id": entity_id, "name": "🛒 Grocery"}))
        self.assertEqual(self.lists()[entity_id]["name"], "🛒 Grocery")
        self.assertEqual(self.call("POST", "/api/lists/rename", {"entity_id": entity_id, "name": "lowes"})[0], 400)
        self.assertEqual(self.call("POST", "/api/lists/rename", {"entity_id": "todo.gone", "name": "Gone"})[0], 400)

    def test_websocket_reports_refused_token(self):
        with mock.patch.object(server, "HA_TOKEN", "wrong"), self.assertRaisesRegex(HomeAssistantError, "token"):
            server.ha_command({"type": "config/entity_registry/update", "entity_id": "todo.x", "name": "X"})

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
        entity_id = self.call("POST", "/api/lists", {"name": "Lowes", "color": "blue"})[1]["entity_id"]
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

    def test_keeps_category_after_its_last_list_leaves(self):
        grocery, lowes = self.ha.add_list("Grocery"), self.ha.add_list("Lowes")
        self.call("PUT", "/api/look", {"entity_id": grocery, "category": "Stores"})
        self.call("PUT", "/api/look", {"entity_id": lowes, "category": "Hardware"})
        self.call("PUT", "/api/look", {"entity_id": grocery, "category": ""})
        self.call("POST", "/api/lists/delete", {"entity_id": lowes})
        self.assertEqual(self.categories(), ["Stores", "Hardware"])
        self.call("POST", "/api/categories/rename", {"from": "Stores", "to": ""})
        self.assertEqual(self.categories(), ["Hardware"])

    def test_keeps_one_order_for_everyone_and_favorites_for_each_person(self):
        grocery, lowes = self.ha.add_list("Grocery"), self.ha.add_list("Lowes")
        self.assertEqual(self.call("PUT", "/api/order", {"order": [lowes, grocery]}), (200, [lowes, grocery]))
        self.assertEqual(self.call("PUT", "/api/favorites", {"entity_id": grocery, "favorite": True}, user="a"),
                         (200, [grocery]))
        self.assertEqual(self.call("GET", "/api/lists", user="b")[1]["order"], [lowes, grocery])
        self.assertEqual(self.call("GET", "/api/lists", user="a")[1]["favorites"], [grocery])
        self.assertEqual(self.call("GET", "/api/lists", user="b")[1]["favorites"], [])
        self.assertEqual(self.call("PUT", "/api/favorites", {"entity_id": grocery, "favorite": "yes"}, user="a")[0],
                         400)

    def test_delete_drops_list_from_order_and_favorites(self):
        entity_id = self.call("POST", "/api/lists", {"name": "Lowes"})[1]["entity_id"]
        self.call("PUT", "/api/order", {"order": [entity_id]})
        self.call("PUT", "/api/favorites", {"entity_id": entity_id, "favorite": True}, user="a")
        self.call("POST", "/api/lists/delete", {"entity_id": entity_id})
        reply = self.call("GET", "/api/lists", user="a")[1]
        self.assertEqual((reply["order"], reply["favorites"]), ([], []))

    def test_reorders_categories(self):
        grocery, fence = self.ha.add_list("Grocery"), self.ha.add_list("Fence")
        self.call("PUT", "/api/look", {"entity_id": fence, "category": "Projects"})
        self.call("PUT", "/api/look", {"entity_id": grocery, "category": "Stores"})
        self.assertEqual(self.categories(), ["Projects", "Stores"])
        self.assertEqual(self.call("PUT", "/api/categories", {"order": ["Stores", "Projects"]}),
                         (200, ["Stores", "Projects"]))
        self.assertEqual(self.categories(), ["Stores", "Projects"])
        self.assertEqual(self.call("PUT", "/api/categories", {"order": ["Stores"]})[0], 400)

    def test_renames_category(self):
        entity_id = self.ha.add_list("Grocery")
        self.call("PUT", "/api/look", {"entity_id": entity_id, "category": "Stores"})
        self.assertEqual(self.call("POST", "/api/categories/rename", {"from": "Stores", "to": "Shops"})[0], 200)
        self.assertEqual(self.lists()[entity_id]["category"], "Shops")

    def test_serves_emoji_picker_with_etag(self):
        path = "/vendor/emoji-picker-element-data/en/emojibase/data.json"
        with urlopen(self.url + path) as response:
            etag, body = response.headers["ETag"], response.read()
            self.assertEqual(response.headers.get_content_type(), "application/json")
        self.assertGreater(len(json.loads(body)), 1000)
        with urlopen(Request(self.url + path, method="HEAD")) as response:
            self.assertEqual((response.headers["ETag"], response.read()), (etag, b""))
        with urlopen(self.url + "/vendor/emoji-picker-element/picker.js") as response:
            self.assertEqual(response.headers.get_content_type(), "text/javascript")
        self.assertEqual(self.call("GET", "/vendor/emoji-picker-element/LICENSE")[0], 404)

    def test_keeps_settings_for_each_person(self):
        defaults = {"layout": "tiles", "order": "added", "favorites": "show", "text": "normal", "theme": "device"}
        self.assertEqual(self.call("GET", "/api/settings", user="a")[1], defaults)
        self.assertEqual(self.call("PUT", "/api/settings", {"layout": "rows"}, user="a")[0], 200)
        self.assertEqual(self.call("PUT", "/api/settings", {"text": "large"}, user="a")[0], 200)
        self.assertEqual(self.call("GET", "/api/settings", user="a")[1],
                         {**defaults, "layout": "rows", "text": "large"})
        self.assertEqual(self.call("GET", "/api/settings", user="b")[1], defaults)
        self.assertEqual(self.call("PUT", "/api/settings", {"layout": "grid"}, user="a")[0], 400)

    def test_export_then_import_into_an_empty_home_assistant(self):
        grocery = self.ha.add_list("🛒 Grocery", items=["Eggs", "Milk"])
        self.ha.lists[grocery]["items"][1]["status"] = "completed"
        self.ha.lists[grocery]["items"][0]["description"] = "A dozen"
        self.ha.add_list("Shopping list", local=False, items=["Soap"])
        self.call("PUT", "/api/look", {"entity_id": grocery, "color": "#3a86ff", "category": "Stores"})
        self.call("PUT", "/api/categories", {"order": ["Stores"]})
        self.call("PUT", "/api/order", {"order": ["todo.shopping_list", grocery]})
        self.call("PUT", "/api/favorites", {"entity_id": grocery, "favorite": True}, user="a")
        self.call("PUT", "/api/settings", {"layout": "rows"}, user="a")
        with urlopen(self.url + "/api/export") as response:
            self.assertIn("lists.json", response.headers["Content-Disposition"])
            exported = json.load(response)
        self.assertEqual((exported["order"], exported["favorites"]), (["Shopping list", "🛒 Grocery"],
                                                                      {"a": ["🛒 Grocery"]}))

        self.ha.lists.clear()
        for name in ("LOOKS_FILE", "CATEGORIES_FILE", "SETTINGS_FILE", "ORDER_FILE", "FAVORITES_FILE"):
            getattr(server, name).unlink()
        self.assertEqual(self.call("POST", "/api/import", exported), (200, {"lists": 2, "items": 3, "skipped": []}))

        items = {item["summary"]: item for item in self.ha.lists["todo.grocery"]["items"]}
        self.assertEqual((items["Eggs"]["status"], items["Eggs"]["description"], items["Milk"]["status"]),
                         ("needs_action", "A dozen", "completed"))
        self.assertEqual([item["summary"] for item in self.ha.lists["todo.shopping_list"]["items"]], ["Soap"])
        lists = self.lists()
        self.assertEqual((lists["todo.grocery"]["color"], lists["todo.grocery"]["category"]), ("#3a86ff", "Stores"))
        self.assertEqual(self.call("GET", "/api/settings", user="a")[1]["layout"], "rows")
        reply = self.call("GET", "/api/lists", user="a")[1]
        self.assertEqual((reply["order"], reply["favorites"]),
                         (["todo.shopping_list", "todo.grocery"], ["todo.grocery"]))

    def test_import_adds_only_what_is_missing(self):
        self.ha.add_list("Grocery", items=["Eggs"])
        with urlopen(self.url + "/api/export") as response:
            exported = json.load(response)
        exported["lists"][0]["items"].append({"summary": "Bread", "status": "needs_action"})
        exported["lists"].append({"name": "Lowes", "items": []})
        self.assertEqual(self.call("POST", "/api/import", exported), (200, {"lists": 1, "items": 1, "skipped": []}))
        self.assertEqual(self.call("POST", "/api/import", exported), (200, {"lists": 0, "items": 0, "skipped": []}))
        self.assertEqual([item["summary"] for item in self.ha.lists["todo.grocery"]["items"]], ["Eggs", "Bread"])

    def test_import_skips_a_list_home_assistant_does_not_create(self):
        self.ha.add_list("Grocery")
        exported = {"version": 1, "lists": [
            {"name": "🛒 Grocery", "items": [{"summary": "Eggs", "status": "needs_action"}]},
            {"name": "Lowes", "items": [{"summary": "Caulk", "status": "needs_action"}]}]}
        self.assertEqual(self.call("POST", "/api/import", exported),
                         (200, {"lists": 1, "items": 1, "skipped": ["🛒 Grocery"]}))
        self.assertEqual(sorted(self.ha.lists), ["todo.grocery", "todo.lowes"])

    def test_import_rejects_other_files(self):
        for body in ({"chores": []}, {"version": 2, "lists": []},
                     {"version": 1, "lists": [{"name": "Grocery", "items": [{"summary": "Eggs", "status": "done"}]}]},
                     {"version": 1, "lists": [], "settings": {"a": {"theme": "blue"}}}):
            with self.subTest(body=body):
                self.assertEqual(self.call("POST", "/api/import", body)[0], 400)
        self.assertEqual(self.ha.lists, {})

    def test_self_check_finds_nothing_wrong(self):
        self.assertEqual(server.find_problem(), "")

    def test_self_check_reports_a_missing_action_and_then_takes_the_report_away(self):
        self.ha.services["todo"].discard("get_items")
        found = server.find_problem()
        self.assertIn("todo.get_items", found)
        server.report(found)
        self.assertIn("todo.get_items", self.ha.notifications["lists_app"]["message"])
        self.ha.services["todo"].add("get_items")
        server.report(server.find_problem())
        self.assertEqual(self.ha.notifications, {})

    def test_self_check_reports_that_local_todo_cannot_create_lists(self):
        self.ha.flow_handlers.remove("local_todo")
        self.assertIn("Local To-do", server.find_problem())

    def test_damaged_data_file_is_reported_and_kept(self):
        entity_id = self.ha.add_list("Grocery")
        server.LOOKS_FILE.write_text("{")
        self.assertIn("looks.json", server.find_problem())
        with mock.patch("traceback.print_exc"):
            self.assertEqual(self.call("PUT", "/api/look", {"entity_id": entity_id, "color": "red"})[0], 500)
        self.assertEqual(server.LOOKS_FILE.read_text(), "{")

    def test_self_check_waits_while_home_assistant_does_not_answer(self):
        self.ha.close()
        with self.assertRaises(Unreachable):
            server.find_problem()

    def test_unexpected_reply_gives_a_clear_error_and_starts_the_self_check(self):
        entity_id = self.ha.add_list("Grocery")
        server.check_now.clear()
        with mock.patch.object(server, "fetch_items", side_effect=KeyError("items")), mock.patch("traceback.print_exc"):
            status, body = self.call("GET", f"/api/items?list={entity_id}")
        self.assertEqual(status, 500)
        self.assertIn("cannot handle", body["error"])
        self.assertTrue(server.check_now.is_set())

    def test_lists_reply_carries_the_problem(self):
        with mock.patch.object(server, "problem", "Home Assistant no longer offers todo.get_items"):
            self.assertIn("todo.get_items", self.call("GET", "/api/lists")[1]["problem"])

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
