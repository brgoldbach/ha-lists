# Lists

A Home Assistant app for shared shopping lists, one for each store. It adds a **Lists** page to the sidebar, with a colored tile for each list.

[![Add this repository to your Home Assistant.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fbrgoldbach%2Fha-lists)

## Features

- Each list is a Local To-do `todo` entity. The stock To-do lists page, automations, and voice assistants use the same lists.
- Changes on either page show on the other within 3 seconds.
- Any household member creates, renames, and deletes lists, also without an admin account.
- Large touch targets, an emoji icon from the list's name, a color for each list, light and dark mode.
- Categories put lists in sections. Each person picks tiles or rows, item order, text size, and theme.

## Install

1. Select the button above, or go to **Settings → Apps → Install app → ⋮ → Repositories** and add `https://github.com/brgoldbach/ha-lists`.
2. Find **Lists** in the app store, then select **Install**.
3. Start it, and turn on **Show in sidebar**.

The app's **Documentation** tab covers use and setup. The same text is in [lists/DOCS.md](lists/DOCS.md).

## Development

The app is a Python standard-library server. Without the Supervisor, it accepts any client and calls the Home Assistant REST API at `HA_URL` and the websocket API at `HA_WS_URL`, with the token in `HA_TOKEN`. Set `HA_TOKEN` to a long-lived access token of an admin user first, because creating, renaming, and deleting lists needs admin rights.

```bash
docker build -t ha-lists lists
docker run --rm -p 8099:8099 -e HA_URL=http://homeassistant.local:8123/api -e HA_WS_URL=ws://homeassistant.local:8123/api/websocket -e HA_TOKEN -v lists-data:/data ha-lists
```

Run the unit tests inside the image. They use a fake Home Assistant API:

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
