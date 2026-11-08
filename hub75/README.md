# HUB75 Album Artwork

Display Music Assistant album covers on a WLED 64x64 HUB75 matrix. The app
automatically follows playing Music Assistant media players and preserves the
display's current brightness. A full-screen cover alternates with a separate
page showing scrolling song title, artist, album, and a large live countdown.
Each page is visible for eight seconds by default; configure `page_interval`
to change this. Disable `show_track_info` for an uninterrupted album cover.

When music stops or pauses, the app displays "music paused" for one minute,
then turns WLED off. Resuming music cancels the shutdown and restores the cover.

Use the `rotation` dropdown to rotate the entire display clockwise by 0, 45,
90, 135, or 180 degrees. Save the configuration and restart the app to apply it.

Set the controller's URL in the Configuration tab, then start the app. There is
no separate web interface or Home Assistant automation to configure.

Read the Documentation tab for configuration, display behavior, and troubleshooting.