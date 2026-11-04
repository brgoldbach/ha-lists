# Lists

A Home Assistant app for shared shopping lists, one for each store. It adds a **Lists** page to the sidebar, with a colored tile for each list.

[![Add this repository to your Home Assistant.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fbrgoldbach%2Fha-lists)

## Features

- Each list is a Local To-do `todo` entity. The stock To-do lists page, automations, and voice assistants use the same lists.
- Changes on either page show on the other within 3 seconds.
- Any household member creates and deletes lists, also without an admin account.
- Large touch targets, an icon and a color for each list, light and dark mode.
- Categories put lists in sections. Each person picks tiles or rows.

## Install

1. Select the button above, or go to **Settings → Apps → Install app → ⋮ → Repositories** and add `https://github.com/brgoldbach/ha-lists`.
2. Find **Lists** in the app store, then select **Install**.
3. Start it, and turn on **Show in sidebar**.

The app's **Documentation** tab covers use and setup. The same text is in [lists/DOCS.md](lists/DOCS.md).

## Development

The app is a Python standard-library server. Without the Supervisor, it accepts any client and calls the Home Assistant API at `HA_URL` with the token in `HA_TOKEN`. Set `HA_TOKEN` to a long-lived access token of an admin user first, because creating and deleting lists needs admin rights.

```bash
docker build -t ha-lists lists
docker run --rm -p 8099:8099 -e HA_URL=http://homeassistant.local:8123/api -e HA_TOKEN -v lists-data:/data ha-lists
```

Run the unit tests inside the image. They use a fake Home Assistant API:

```bash
docker run --rm -v $PWD/lists/tests:/tests --entrypoint python3 ha-lists -m unittest discover -s /tests
```

To publish an update, raise `version` in [lists/config.yaml](lists/config.yaml) and add an entry to [lists/CHANGELOG.md](lists/CHANGELOG.md). Home Assistant then offers the update.

## License

[MIT](LICENSE)
