# Garmin Gear Fixer

A small local app for reviewing walking activities and changing their Garmin gear. It uses the unofficial [garminconnect](https://pypi.org/project/garminconnect/) package. Requires Python 3.12 or newer and internet access.

## Run

Download or clone this repository, then open a terminal in the project folder. Python 3.12 or newer must be installed.

### macOS and Linux

```sh
./run.sh
```

The first run creates `.venv` and installs the dependencies from PyPI. Each later run checks that they are installed, so a failed installation can be retried by running the command again.

### Windows

1. Install [Python](https://www.python.org/downloads/) 3.12 or newer if you don't already have it. Include the Python launcher when installing.
2. Open PowerShell in the project folder. For example, in File Explorer, open the folder, click the address bar, type `powershell`, and press Enter.
3. Run these commands once to create the virtual environment and install the dependencies:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

   If `py -3 --version` shows a version older than 3.12, install a newer version and select it when creating the environment, for example `py -3.12 -m venv .venv` for Python 3.12.
4. Start the app with this command. Run it again whenever you want to use the app:

   ```powershell
   .\.venv\Scripts\python.exe app.py
   ```

On either platform, enter your Garmin email, password, and MFA code in the terminal when prompted. The app does not save your password; Garmin session tokens are stored in the ignored `.state` folder. Delete `.state` to sign out and clear the activity cache. The page opens at http://127.0.0.1:8765; press Ctrl+C in the terminal to stop.

## Optional defaults

Copy `.env.example` to `.env` and fill in any defaults you want. `.env` is ignored by Git and should stay private. Environment variables override the file.

- `DEFAULT_GEAR_ID`: Garmin gear UUID to select initially. When empty, select the original gear on the page.
- `LOCATION_LATITUDE` and `LOCATION_LONGITUDE`: decimal coordinates for the initial location. Set both, or leave both empty.
- `LOCATION_RADIUS_METERS`: initial radius in metres; defaults to 50.

To find a gear ID, open [Garmin Connect](https://connect.garmin.com/), go to your gear, and open the item you want. The web address ends in its gear ID, for example:

```text
https://connect.garmin.com/app/gear/detail/<gear-id>
```

Copy the part after the final `/` into `DEFAULT_GEAR_ID` in `.env`. It is a UUID (universally unique identifier): a long identifier made of letters and numbers, usually separated by hyphens, that Garmin uses to identify that gear item. You can leave this setting empty and choose gear on the page instead.

The location and radius can also be entered on the page. Open **Choose a location on the map** and click to set coordinates, or type them directly. Clear both coordinate fields to disable location filtering. Changing them takes effect when you load walks again. When no location is set, the proximity filter and activity status are hidden.

## Review and change gear

Select the original gear and an inclusive local-date range, then load walks. Check the activities to change, choose replacement gear, and apply it to all selected activities. Filters do not clear selections; the apply button includes selections hidden by filters. A confirmation shows the number of selected and hidden activities.

When a location is set, proximity means any point on the recorded track, including between consecutive recorded points within a track segment, comes within the chosen radius. Missing or unreadable GPS data is shown as Unknown. Maps use locally bundled Leaflet; map tiles are fetched from OpenStreetMap, which receives tile requests for displayed locations. The date range defaults to the current calendar year. Time filters use local activity time and support overnight ranges.

The app checks current gear again, adds and verifies the replacement, then removes and verifies the selected original. Other gear is preserved. Individual failures are displayed; a partial change may require checking Garmin before retrying.

The first load for a source and date range fetches each walk's gear and matching tracks, then saves a local cache in `.state/activities.json`. Later loads of the same source and exact date range reuse it, including after restart. Changing the location or radius recalculates proximity from cached routes. Use **Refresh from Garmin** to see new or externally edited activities, updated gear, or retry missing routes. The cache contains activity names, dates, times, and GPS tracks; delete `.state/activities.json` to clear it. Successful changes update the cache and failures invalidate it. Garmin gear is still checked again before every change.

## Development

Run `./.venv/bin/python -m unittest -v` on macOS or Linux, or `.\.venv\Scripts\python.exe -m unittest -v` in Windows PowerShell, to execute the tests. The bundled Leaflet library is distributed under its own [license](static/LEAFLET-LICENSE.txt).

## License

The project code is licensed under the [MIT License](LICENSE). The bundled Leaflet library has its own [BSD 2-Clause license](static/LEAFLET-LICENSE.txt).
