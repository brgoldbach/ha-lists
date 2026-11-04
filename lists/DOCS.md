# Lists

Shared shopping lists in Home Assistant, one for each store. Every person in the household sees and edits the same lists.

## How it works

Each list is a Home Assistant to-do list, a `todo` entity from the **Local To-do** integration. The app keeps no copy of the items. It reads and writes the same lists as the stock **To-do lists** page:

| Change | Where it shows |
|---|---|
| Add, check off, or remove an item on either page | On both pages. An open Lists page updates within 3 seconds. |
| Create a list on the Lists page | In To-do lists, and under **Settings > Devices & services > Local To-do** |
| Create a list in To-do lists | On the Lists page, with a 📝 icon and a color from its name |
| Delete a list on either page | Gone from both pages |

Automations, scripts, and voice assistants use the lists through the `todo` actions, for example `todo.add_item` with `entity_id: todo.grocery`.

The Lists page also shows to-do lists of other integrations, for example the Shopping list. You can add and check off their items. You cannot delete those lists here; remove them in their own integration.

## Who can do what

The app talks to Home Assistant as the Supervisor, which has admin rights. So any person who can open the Lists page can create and delete lists. They do not need an admin account. Home Assistant itself lets only admins create or delete to-do lists.

## Install

1. Go to **Settings > Apps > Install app**.
2. In the top-right menu, select **Repositories**.
3. Add `https://github.com/brgoldbach/ha-lists`.
4. Select **Lists**, then **Install**.
5. On the **Info** tab, turn on **Show in sidebar**, then select **Start**.

**Lists** now shows in the sidebar of every user, admins and non-admins.

## A clean sidebar for each person

Each person can hide the sidebar items they do not use. The setting belongs to the person's account and applies on all their devices.

1. Sign in as that person.
2. Press and hold the sidebar header (**Home Assistant**). Or select the person's name at the bottom of the sidebar, then **Edit** next to **Change the order and hide items from the sidebar**.
3. Hide every item except **Lists** and the others the person uses.
4. Drag **Lists** to the top, then save.

Home Assistant cannot open an app page at sign-in, so the person opens Home Assistant, then taps **Lists**.

## Use

| To | Do this |
|---|---|
| Add a store list | Tap **New list**. Type the name, pick a category, an icon, and a color, then tap **Create**. |
| Use an emoji that is not in the grid | Tap **All emoji** to search every emoji. Or type one in the box: on a phone, switch the keyboard to emoji; on Windows, press **Win + .**; on a Mac, press **Ctrl + Cmd + Space**. A web page cannot open the emoji keyboard itself. |
| Show lists as rows or tiles | Tap the layout button at the top right of the Lists page. Each person keeps their own choice on all their devices. |
| Put lists in sections | Give each list a category, for example Stores or Projects. Type a new category, or tap an existing one under the box. The Lists page shows a section for each category in name order. Lists without a category show last, under **Other**. If no list has a category, the page shows no sections. |
| Add an item | Open the list. Type in **Add an item**, then tap **+** or press Enter. The box stays open for the next item. |
| Check off an item | Tap the item. It moves to **Got it**. Tap it again to put it back. |
| Remove an item | Tap **×** on its row. |
| Remove all checked items | Tap **Clear** next to **Got it**. |
| Change the icon, color, or category | Open the list. Tap **⋯**, then **Edit list**. |
| Rename a category | Tap the category name above its section (✎). The new name applies to every list in it. Leave the name blank to remove the category. |
| Hide a list from this page | Tap **⋯**, then **Hide list**. Hidden lists show under **Hidden lists** at the bottom of the page. The list stays in Home Assistant. To show it again, open it, then tap **⋯** and **Show list**. |
| Delete a list | Tap **⋯**, then **Delete list**, then **Delete**. |

Deleting a list also deletes all its items. Only a Home Assistant backup brings them back.

## Data

| What | Where |
|---|---|
| Lists and items | Home Assistant: `.storage/local_todo.<name>.ics`, one file for each list |
| Icon, color, category, and hidden state of each list | The app's `/data/looks.json`. Everyone sees the same. |
| Tiles or rows, for each person | The app's `/data/layouts.json` |

A Home Assistant backup that includes this app saves both.
