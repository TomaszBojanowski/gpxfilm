# GPX variants for the reader tests

Small synthetic files for `tests/test_gpx_read.py`. All of them are written by `make_variants.py` from made-up
coordinates (a short straight walk starting at 49.5 N, 20.0 E); none is a copy of a real export. The second column says
which kind of real file each one imitates.

| File | Imitates | Expected |
|---|---|---|
| `plain.gpx` | an ordinary recorded activity | read as is |
| `plain.gpx.gz` | Strava bulk export (`activities/*.gpx.gz`) | read like `plain.gpx` |
| `broken.gpx.gz` | a cut-off download of a `.gpx.gz` | message: broken gzip archive |
| `leading_space_bom.gpx` | files re-saved by editors: UTF-8 BOM and blank lines before the XML declaration | read like `plain.gpx` |
| `empty.gpx` | an empty file | message: the file is empty |
| `truncated.gpx` | an interrupted recording or download | message: broken or incomplete XML |
| `indoor_empty_trkseg.gpx` | Garmin Connect, Polar Flow, RunGap indoor activity (`<trkseg/>`) | message: activity without GPS |
| `waypoints_only.gpx` | saved places only (`wpt`, no track) | message: only waypoints |
| `one_point.gpx` | an activity with a single GPS fix | message: only one point |
| `activity.tcx` | TCX export (Garmin Connect, Polar Flow, COROS) | message: TCX, export GPX instead |
| `route.kml` | KML from Google Earth or a watch app | message: KML, convert to GPX |
| `activity.fit` | FIT file from a watch or bike computer (header only) | message: FIT, export GPX instead |
| `export.zip` | Garmin Connect "Export Original" (ZIP with FIT) or KMZ | message: unpack the archive |
| `not_gpx.xml` | some other XML file | message: not GPX, names the root element |
| `bad_points.gpx` | single broken fixes: no or empty `lat`, text, `nan`, `inf`, out of range, `0, 0`, comma decimals; a `wpt` without `lat` | broken points skipped with a count, the rest read like `plain.gpx` |
| `indoor_no_coords.gpx` | HealthFit or Amazfit indoor workout: `trkpt` with time and heart rate but no position | message: no point has valid coordinates |
| `same_time.gpx` | Strava zero-duration activity, AllTrails track with the export time on every point | drawn without times, no crash |
| `backwards_time.gpx` | times running backwards | drawn without times, no crash |
| `nan_ele.gpx` | `nan` and `inf` as elevation on single points | counted as missing elevation |
| `time_formats.gpx` | time notations seen in exports or allowed by ISO 8601: `z`, 1–9 fraction digits (Garmin, Sports Tracker), comma, `+0200`, `+02`, `-01:30`, space instead of `T`, no zone, spaces around | every point read with its exact time, the same on Python 3.10–3.14 |
| `partial_time.gpx` | a recording with one point without `<time>` | that point dropped, times kept |
| `sparse_time.gpx` | times on only a quarter of the points | drawn as a route without times |
| `zepp_duplicates.gpx` | Zepp Life: every fix written twice, first without elevation | the two copies merged into one point with elevation |
| `ele_zero.gpx` | Polar Flow without altitude: `0.0` on every point | elevation from the terrain model, with a message |
| `ele_sentinel.gpx` | Zepp Life: `-20000` on the first points | those two values treated as missing, the rest kept |
| `ele_all_sentinel.gpx` | `-20000` on every point | elevation from the terrain model |
| `ele_short_gap.gpx` | elevation missing on two neighboring points | gap filled |
| `ele_long_gap.gpx` | elevation missing over about 300 m | elevation from the terrain model, with a message |
| `ele_sparse.gpx` | Health Sync (Huawei): elevation on every fifth point | elevation from the terrain model, with a message |
| `ele_negative.gpx` | Polar Flow near sea level: small negative values mixed with `0.0` | kept |
| `fake_1970.gpx` | Locus Map planned route: times from 1 January 1970 | times dropped with a message naming `--keep-times` |
| `fake_ms_ramp.gpx` | old Garmin Connect course: times a few milliseconds apart | times dropped (thousands of km/h between points) |
| `fake_years_gap.gpx` | a Wikiloc trail extended years later | times dropped (gap of thousands of days) |
| `fast_car.gpx` | a drive at about 130 km/h | times kept |
| `hr_garmin_ns3.gpx` | Garmin Connect, Zepp: `ns3:hr` next to `ns3:atemp` and `ns3:cad` | heart rate read |
| `hr_gpxdata.gpx` | COROS (2024+), old Movescount: `gpxdata:hr` directly under `<extensions>` | heart rate read |
| `hr_heartrate.gpx` | COROS (around 2022): unprefixed `<heartrate>` | heart rate read |
| `hr_heart_rate.gpx` | `<heart_rate>` written by some converters | heart rate read |
| `hr_heatrate.gpx` | COROS misspelling `gpxdata:heatrate` | heart rate read |
| `hr_invalid.gpx` | Strava `255`, COROS `0`, `-nan`, text, `300` | treated as missing, short gaps filled |
| `hr_fractional.gpx` | Movescount: heart rate with decimals | read as is |
| `hr_sparse.gpx` | Health Sync (Huawei): heart rate on every fifth point (50 s apart) | kept, gaps filled |
| `hr_too_sparse.gpx` | heart rate on every tenth point | below 20%: no heart rate |
| `hr_long_gap.gpx` | strap dropout of 160 s | no heart rate in the gap (not filled) |
| `multi_touching.gpx` | two tracks of one walk (the second starts about 300 m from the end of the first) | listed, joined |
| `multi_unordered.gpx` | the same two tracks written in the wrong order (files merged by hand) | listed in time order, joined |
| `multi_days.gpx` | Garmin handheld archive or gpx.studio merge: one track per day | joined, the nights become stops |
| `multi_apart.gpx` | tracks far apart in one file (a short one, then a long one with a continuation) | the longest group drawn; `--track N` draws exactly track N |
| `multi_no_times.gpx` | two planned tracks without times | listed in file order, the longest drawn |
| `segments.gpx` | Garmin handheld: one track with several `trkseg` (pauses) | joined without a list |
| `basecamp_routes.gpx` | Garmin BaseCamp: several `rte`, `rtept` times are when the points were created | listed, the longest drawn, times ignored |
| `routes_touching.gpx` | two planned routes, the second starting where the first ends | listed, joined (the message uses the feminine form for routes) |
| `trk_and_rte.gpx` | OsmAnd planned route: dense `trk` plus sparse `rte` | the track drawn |
| `metadata_name.gpx` | Zepp Life: the app name in `metadata/name`, the activity name in `trk/name` | title from the track |
| `metadata_name_only.gpx` | name only in `metadata` | title from `metadata` |
| `route_trk_no_time.gpx` | Strava route, Garmin Connect course, Komoot planned tour, mapy.com: `trk` with elevation and no times | drawn as a route without times |
| `route_rte_ele.gpx` | Suunto app route, AllTrails: `rte` with elevation | drawn as a route without times |
| `route_rte_bare.gpx` | Suunto app route without elevation | elevation from the terrain model |
| `basecamp_rpt.gpx` | Garmin BaseCamp: via points with the road in `gpxx:rpt` | the road followed, not straight lines between via points |
| `corrupt_deflate.gpx.gz` | a `.gpx.gz` with damaged compressed data (header intact) | message: broken gzip archive |
| `activity_header12.fit` | FIT file from an older device (12-byte header that starts with a form feed) | message: FIT, export GPX instead |
| `shift_jis.gpx` | Japanese tools (e.g. Kashmir 3D) writing Shift_JIS | read, Japanese name kept |
| `unknown_encoding.gpx` | an encoding name Python does not know | message naming the encoding |
| `shift_jis_windows.gpx` | Japanese Windows tools: `Shift_JIS` label with characters only the Windows code page has (①), spaces around `=` in the declaration | read, name kept |
| `windows_31j.gpx` | the same with the label `Windows-31J` | read, name kept |
| `shift_jis_truncated.gpx` | a cut-off Shift_JIS file | message: broken or incomplete XML |
| `shift_jis_cut_in_char.gpx` | a Shift_JIS file cut off in the middle of a two-byte character | message: broken or incomplete XML |
| `big5_windows.gpx` | Taiwanese Windows tools: `Big5` label with characters only the Windows code page has (恒) | read, name kept |
| `shift_jis_bad_bytes.gpx` | a Shift_JIS file with bytes that are not Shift_JIS | message: the characters do not match the encoding |
| `one_bad_time.gpx` | one point with epoch 0 (`1970-01-01`) in a 2025 recording | that point dropped, times kept |
| `first_bad_time.gpx` | the first point with a GPS week-rollover date | that point dropped, times kept |
| `bad_time_run.gpx` | three neighboring points with epoch 0 in a 2025 recording | those points dropped, times kept |
| `bad_time_start.gpx` | a logger whose first two fixes still carry an old date | those points dropped, times kept |
| `bad_time_second_last.gpx` | epoch 0 on the second-to-last point | only that point dropped, the finish kept |
| `bad_time_two_kinds.gpx` | epoch 0 and a GPS week-rollover date on two neighboring points | those points dropped, times kept |
| `bad_time_start_and_middle.gpx` | epoch 0 on the first two points and again in the middle | those points dropped, times kept |
| `late_extension.gpx` | a 2014 track extended in 2025 by three points | the three points dropped as broken times (at most 3 points, or 1% of the file, at the start or end of a track), also with `--keep-times` |
| `days_broken_edges.gpx` | one track per day; the first day starts and the second ends with four epoch-0 fixes | those points dropped, times kept |
| `bad_time_end_run.gpx` | the last four of 200 fixes at epoch 0 (more than the three dropped at an end) | times kept; the four points going back in time are left out of the drawing |
| `late_start.gpx` | the first six fixes dated 2038, the rest in 2025 | times dropped as made up (the jump back), no point lost |
| `touching_days_apart.gpx` | a short warm-up track and the main track ten days later, touching | joined, times dropped as made up, no point lost |
| `year_one.gpx` | .NET placeholder `0001-01-01` as time, next to a normal track | no time, no crash in the track list |
| `fake_with_untimed.gpx` | planned route with 1970 times and one point without time | times dropped, no point lost |
| `polar.gpx` | points at `-90` and `89.9` latitude (beyond the map) | skipped with a count of points beyond the map |
| `north_pole.gpx` | the North Pole Marathon: every point at 89.5 N | message: the map does not reach that far |
| `north_pole_glitch.gpx` | the same with one `0, 0` fix at the start | message: the map does not reach that far |
| `polar_waypoint_only.gpx` | waypoints only, one of them at the North Pole | message: only waypoints |
| `pole_waypoint.gpx` | a Svalbard track with a waypoint at the North Pole | the waypoint counted as beyond the map, not as broken |
| `one_place.gpx` | every fix within 15 m, the same elevation everywhere | message: all points in one place, nothing about elevation |
| `one_long_stop.gpx` | a recording that drifts 29 m in 10 minutes at one elevation | message: the whole recording is one stop, nothing about elevation |
| `hr_isolated.gpx` | heart rate on every fourth point, 80 s apart (smart recording with sparse heart rate) | below 20% usable: no heart rate, with a message |
| `hr_no_times.gpx` | a track without times with single heart-rate gaps | gaps up to 200 m filled |
| `hr_no_times_long_gap.gpx` | a track without times with a heart-rate gap of about 300 m | that gap stays without a reading |
| `hr_dense_then_none.gpx` | smart recording: heart rate every 2 s while fast, then the strap stops and points come every 20 s | heart rate kept: 17% of the time but 40% of the line has a reading |
| `hr_pairs.gpx` | heart rate on two points in eight (27%), 70 s gaps between the pairs | under 20% of the line has a reading: no heart rate, with a message |
| `switchbacks_stop_spike.gpx` | a walk up 12 switchbacks with a 5-minute stop and one fix thrown 300 m off, a fix every 2 s with 1 m of noise | distance as a watch counts it: every segment except the drift during the stop and the spike |
| `spike_series.gpx` | three neighboring fixes thrown 120 m to the side (multipath), a fix every second | the three fixes are a spike |
| `cold_start.gpx` | the first fix 500 m away, where the watch was last used | the first fix is a spike |
| `relocated_end.gpx` | a walk, then the watch paused, carried 3 km and one more fix there | the last fix is a spike |
| `car_spike.gpx` | a drive with a fix every 10 s and one fix 1.5 km off | a spike by speed (540 km/h), though within eight car steps |
| `sparse_spur.gpx` | running with a fix every 60 s, with a 150 m spur to a viewpoint | no spike: the spur is real |
