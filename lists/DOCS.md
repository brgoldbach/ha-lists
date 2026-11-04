# Lists

Shared shopping lists in Home Assistant, one for each store. Every person in the household sees and edits the same lists.

## How it works

Each list is a Home Assistant to-do list, a `todo` entity from the **Local To-do** integration. The app keeps no copy of the items. It reads and writes the same lists as the stock **To-do lists** page:

| Change | Where it shows |
|---|---|
| Add, check off, or remove an item on either page | On both pages. An open Lists page updates within 3 seconds. |
| Create a list on the Lists page | In To-do lists, and under **Settings > Devices & services > Local To-do** |
| Create or rename a list in To-do lists or in Settings | On the Lists page |
| Delete a list on either page | Gone from both pages |

Automations, scripts, and voice assistants use the lists through the `todo` actions, for example `todo.add_item` with `entity_id: todo.grocery`.

The Lists page also shows to-do lists of other integrations. You can add and check off their items. You cannot delete those lists here; remove them in their own integration.

## The built-in Shopping list

Home Assistant sets up its Shopping list integration at onboarding, with one list, `todo.shopping_list`. The store lists replace it, so the Lists page hides it at first. To use it, open **Hidden lists** at the bottom of the page, open the list, then tap **⋯** and **Show list**.

A hidden Shopping list still takes items from anything that targets it. For example:

- the voice command "add milk to my shopping list", which the Shopping list integration registers;
- the `shopping_list.*` actions in automations and scripts;
- the Shopping list card on a dashboard.

Those items go to the hidden list, not to your grocery list. Before you use voice or such automations, do one of these:

- Rename the Shopping list to your grocery list, for example "🛒 Grocery", and show it. It keeps its entity ID, so the commands above add to it. For voice, also give it the alias "Grocery" in Home Assistant (**Settings > Entities >** the list **> Voice assistants**).
- Or remove the Shopping list integration in **Settings > Devices & services**, and give your Local To-do grocery list the alias "shopping list". Then test the voice command: this route is not tested.

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

## Lists

| To | Do this |
|---|---|
| Add a list | Tap **+** at the top right of the Lists page. Type the name, pick a category and a color, then tap **Create**. |
| Give a list an icon | Put an emoji in its name, for example "🛒 Grocery". The first emoji is the icon, and the page shows the name without it. A name can have more emoji, for example "🛒 Grocery 🥕". A list without an emoji shows its first letter. |
| Type an emoji | On a phone, use the emoji keyboard. On a computer, put the cursor where the emoji goes, then select **😀** next to the name to search all emoji. |
| Pick a color | Tap one of the 16 colors, or the rainbow swatch for any color from the device's color picker. On a light color, the text turns dark. |
| Make a list a favorite | Open it, then tap **☆** next to **⋯**. The star fills gold (**★**), and the list moves to the **★ Favorites** section at the top of the page, with a small gold ★ on it. If you hide that section in the settings, the list stays in its own section, still with its ★. Tap **★** to undo. Each person has their own favorites. |
| Change a list | Open it. Tap **⋯**, then **Edit list**. A new name also shows on the stock To-do page and to voice assistants. The entity ID stays the same. |
| Hide a list | Tap **⋯**, then **Hide list**. It moves to **Hidden lists** at the bottom of the page, and stays in Home Assistant. To show it again, open it, then tap **⋯** and **Show list**. |
| Delete a list | Tap **⋯**, then **Delete list**, then **Delete**. This also deletes all its items. Only a Home Assistant backup brings them back. |

## Items

| To | Do this |
|---|---|
| Add an item | Open the list. Type in **Add an item**, then tap **+** or press Enter. The box stays open for the next item. |
| Check off an item | Tap it. It moves to **Got it**. Tap it again to put it back. |
| Remove an item | Tap **×** on its row. |
| Remove all checked items | Tap **Clear** next to **Got it**. |

## Sections

Give lists a category, for example Stores or Projects, and the Lists page shows a section for each. Type a new category in the list's **Category** box, or tap an existing one under the box. Lists without a category show last, under **Other**. A category stays, also with no lists, until you remove it.

To drag on a phone, hold the list or heading still for a moment, then drag. With a mouse, drag at once.

| To | Do this |
|---|---|
| Change the order of the lists | Drag a list onto another list. It takes that place: a bar shows on the side where it lands. Everyone sees the same order. |
| Move a list to another section | Drag it onto the section, or onto a list in it. Drop it on **Other** to remove its category. |
| Make a list a favorite by drag | Drag it onto **★ Favorites**. Drag it out to a section to undo. |
| Change the order of the sections | Drag a section by its heading (⠿). A new category goes at the end, and **Other** always stays last. |
| Rename or remove a category | Tap its heading (✎). The new name applies to every list in it. To remove the category, clear the name; its lists move to **Other**. |

## Settings

Tap the settings button at the top right of the Lists page. Each person keeps their own settings on all their devices.

| Setting | Choices |
|---|---|
| Layout | Tiles or rows |
| Density | Normal, or compact: smaller lists and text. A row shows its name on one line, a tile on up to two. |
| Item order | As added, or A–Z. A–Z changes only this page; Home Assistant keeps the order the items were added. |
| Favorites section | Show or hide the **★ Favorites** section. When it is hidden, your favorites stay in their own sections. The stars on lists and the star button show either way. |
| Text size | Normal or large |
| Theme | Follow the device, light, or dark |

## Backup

Under **Backup** in the settings:

- **Export** saves a JSON file with every list, item, color, and category, the order of the lists and categories, and each person's favorites and settings.
- **Import** reads such a file. It adds the lists and items that are missing, matched by name, and takes the colors, categories, orders, favorites, and settings from the file. It never deletes or changes items that are already there. If Home Assistant does not create a list, for example because its name is too close to one it has, import skips that list, names it, and brings in the rest.

Use them to move the lists to another Home Assistant, or to keep a copy apart from Home Assistant backups.

## Data

| What | Where |
|---|---|
| Lists and items | Home Assistant: `.storage/local_todo.<name>.ics`, one file for each list |
| Color, category, and hidden state of each list | The app's `/data/looks.json`. Everyone sees the same. |
| Categories and their order, also empty ones | The app's `/data/categories.json`. Everyone sees the same. |
| Order of the lists | The app's `/data/order.json`. Everyone sees the same. |
| Favorites, for each person | The app's `/data/favorites.json` |
| Settings, for each person | The app's `/data/settings.json` |

A Home Assistant backup saves all of it, if the backup includes Home Assistant and this app.

## When Home Assistant changes

The app uses Home Assistant's to-do actions, the Local To-do integration, and a few Home Assistant APIs. A Home Assistant update can change one of them. So the app checks that all of them still work: when it starts, every hour, and at once after an unexpected error.

If something is missing, the app reports it in three places:

- a notification in Home Assistant, **Lists app needs an update**, under the bell in the sidebar;
- a red banner at the top of the Lists page;
- the app's **Log** tab.

The notification goes away by itself when the check passes again. While Home Assistant restarts, the app waits and checks again; that is not a problem.

Your data stays safe:

- The lists and items live in Home Assistant. The app never deletes them, except when you delete a list.
- The app writes each of its files in one step, so a failure never leaves half a file.
- The app does not write over a damaged file. It reports the file instead. Restore it from a Home Assistant backup, or import an export.

When you see the report, look for an update of the Lists app. An export from **Backup** in the settings is a good copy to keep meanwhile.
