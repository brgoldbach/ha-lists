# Lists

A Home Assistant app for shared shopping lists, one for each store. It adds a **Lists** page to the sidebar, with a colored tile or row for each list.

[![Add this repository to your Home Assistant.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fbrgoldbach%2Fha-lists)

## Features

- Each list is a Local To-do `todo` entity. The stock To-do lists page, automations, and voice assistants use the same lists.
- Changes on either page show on the other within 3 seconds.
- Any household member creates, renames, and deletes lists, also without an admin account.
- An emoji icon from the list's name, a color for each list, and categories that put lists in sections.
- Each person picks tiles or rows, item order, text size, and theme.
- Export and import of everything as a JSON file.

## Install

1. Select the button above, or go to **Settings → Apps → Install app → ⋮ → Repositories** and add `https://github.com/brgoldbach/ha-lists`.
2. Find **Lists** in the app store, then select **Install**.
3. Start it, and turn on **Show in sidebar**.

The app's **Documentation** tab covers use and setup. The same text is in [lists/DOCS.md](lists/DOCS.md).

## Development

| Path | Content |
|---|---|
| [lists/config.yaml](lists/config.yaml) | The app's manifest. Home Assistant builds the image from [lists/Dockerfile](lists/Dockerfile) at install. |
| [lists/app/server.py](lists/app/server.py) | Python standard-library server: the page, a JSON API, and the calls to Home Assistant |
| [lists/app/static/index.html](lists/app/static/index.html) | The page: HTML, CSS, and JavaScript in one file |
| [lists/tests](lists/tests) | Unit tests, against a fake Home Assistant API in `fake_ha.py` |

Without the Supervisor, the server accepts any client. It calls the Home Assistant REST API at `HA_URL` and the websocket API at `HA_WS_URL`, with the token in `HA_TOKEN`. Use a long-lived access token of an admin user, because creating, renaming, and deleting lists needs admin rights.

```bash
docker build -t ha-lists lists
docker run --rm -p 8099:8099 -e HA_URL=http://homeassistant.local:8123/api -e HA_WS_URL=ws://homeassistant.local:8123/api/websocket -e HA_TOKEN -v lists-data:/data ha-lists
```

Run the unit tests inside the image:

```bash
docker run --rm -v $PWD/lists/tests:/tests --entrypoint python3 ha-lists -m unittest discover -s /tests
```

To publish an update, raise `version` in [lists/config.yaml](lists/config.yaml) and add an entry to [lists/CHANGELOG.md](lists/CHANGELOG.md). Home Assistant then offers the update.

## License

[MIT](LICENSE)

The app includes, unchanged, two packages under the Apache License 2.0. Their license files are next to them in [lists/app/static/vendor](lists/app/static/vendor):

| Package | Version | Files |
|---|---|---|
| [emoji-picker-element](https://github.com/nolanlawson/emoji-picker-element) | 1.29.1 | `picker.js`, `database.js` |
| [emoji-picker-element-data](https://github.com/nolanlawson/emoji-picker-element-data) | 1.8.0 | `en/emojibase/data.json` |
