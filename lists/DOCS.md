# Lists

Shared shopping lists in Home Assistant, one for each store. Every person in the household sees and edits the same lists.

## How it works

Each list is a Home Assistant to-do list, a `todo` entity from the **Local To-do** integration. The app keeps no copy of the items. It reads and writes the same lists as the stock **To-do lists** page:

| Change | Where it shows |
|---|---|
| Add, check off, or remove an item on either page | On both pages. An open Lists page updates within 3 seconds. |
| Create a list on the Lists page | In To-do lists, and under **Settings > Devices & services > Local To-do** |
| Create or rename a list in To-do lists or in Settings | On the Lists page, with a color from its entity ID |
| Delete a list on either page | Gone from both pages |

Automations, scripts, and voice assistants use the lists through the `todo` actions, for example `todo.add_item` with `entity_id: todo.grocery`.

The Lists page also shows to-do lists of other integrations, for example the Shopping list. You can add and check off their items. You cannot delete those lists here; remove them in their own integration.

## Who can do what

The app talks to Home Assistant as the Supervisor, which has admin rights. So any person who can open the Lists page can create, rename, and delete lists. They do not need an admin account. Home Assistant itself lets only admins do that.

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
| Add a store list | Tap **+** at the top right of the Lists page. Type the name, pick a category and a color, then tap **Create**. |
| Move a list to another section | Drag it onto the section. On a phone, hold the list still for a moment, then drag. Drop it on **Other** to remove its category. |
| Change the order of the sections | Drag a section by its heading (⠿). On a phone, hold the heading still for a moment, then drag. **Other** always stays last. |
| Pick a color | Tap one of the 16 colors, or the rainbow swatch for any color. The rainbow opens the device's color picker. On a light color, the list's text turns dark. |
| Give a list an icon | Put an emoji in its name, for example "🛒 Grocery". The first emoji in the name is the icon, and the page shows the name without it. A list without an emoji shows its first letter. On a phone, use the emoji keyboard. On a computer, put the cursor where the emoji goes, then select **😀** next to the name to search all emoji. A name can have more than one emoji, for example "🛒 Grocery 🥕"; only the first one is the icon. |
| Change settings | Tap the settings button at the top right of the Lists page. **Layout**: tiles or rows. **Item order**: as added, or A–Z (only on this page; Home Assistant keeps the added order). **Text size**: normal or large. **Theme**: follow the device, light, or dark. Each person keeps their own settings on all their devices. |
| Put lists in sections | Give each list a category, for example Stores or Projects. Type a new category, or tap an existing one under the box. The Lists page shows a section for each category, in the order you set; a new category goes at the end. Lists without a category show last, under **Other**. A category stays, also with no lists in it, until you remove it. If there are no categories, the page shows no sections. |
| Add an item | Open the list. Type in **Add an item**, then tap **+** or press Enter. The box stays open for the next item. |
| Check off an item | Tap the item. It moves to **Got it**. Tap it again to put it back. |
| Remove an item | Tap **×** on its row. |
| Remove all checked items | Tap **Clear** next to **Got it**. |
| Rename a list, or change its emoji, color, or category | Open the list. Tap **⋯**, then **Edit list**. A new name also shows on the stock To-do page and to voice assistants. The entity ID stays the same. |
| Rename or remove a category | Tap the category name above its section (✎). The new name applies to every list in it. To remove the category, clear the name; its lists move to **Other**. |
| Hide a list from this page | Tap **⋯**, then **Hide list**. Hidden lists show under **Hidden lists** at the bottom of the page. The list stays in Home Assistant. To show it again, open it, then tap **⋯** and **Show list**. |
| Delete a list | Tap **⋯**, then **Delete list**, then **Delete**. |

Deleting a list also deletes all its items. Only a Home Assistant backup brings them back.

## Data

| What | Where |
|---|---|
| Lists and items | Home Assistant: `.storage/local_todo.<name>.ics`, one file for each list |
| Color, category, and hidden state of each list | The app's `/data/looks.json`. Everyone sees the same. |
| Categories, also empty ones | The app's `/data/categories.json`. Everyone sees the same. |
| Settings, for each person | The app's `/data/settings.json` |

A Home Assistant backup that includes this app saves both.
