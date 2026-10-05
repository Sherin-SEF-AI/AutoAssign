# Assumptions

Every assumption made while building blurabbit-dispatch, grouped by area. Each one is a
default that can be changed through configuration or the data contract unless noted.

## Data and synthetic world

- A1. **ETS trips are not account restricted.** ETS runs carry `tags.ets_client` (four synthetic
  ETS clients) and `tags.series_id`. `account_id` is reserved for the corporate accounts A1 to A5
  whose trips may only be served by that account's three approved drivers.
- A2. **ETS series run every day of the week**, so volume stays near 120 trips per day. About 46
  base series (half morning, half evening) with each series kept on a given day with probability
  0.9 (the 10% churn).
- A3. **Driver weekly off rotates by ISO week**: driver `i` is off when `(i + iso_week) mod 7`
  equals the weekday. This yields 25 or 26 drivers on duty every day.
- A4. **Uber shifts**: on half of the days one on-duty driver has `channel_today = uber` and is
  excluded from planning. The spec does not give a rate.
- A5. **Hub AC point count**: each hub has 6 AC 7.2 kW points (the spec gives the power only) and
  2 DC 30 kW points.
- A6. **Vehicle classes**: Windsor and ZS EV are `suv`; Tigor EV and eC3 are `sedan`. Trips with
  `vehicle_class = any` can use either.
- A7. **`DataSource.list_vehicles()` has no date argument**, so it returns the fleet's current
  status. For the synthetic source this is the status on today's IST date (the 10% service days
  are evaluated for today). Seeded historical snapshots use each day's own status.
- A8. **SOC check-ins** are reported 20 minutes before shift start, only for on-duty drivers. The
  nightly solve always assumes 100% (overnight charging policy) and ignores check-ins; the 06:00
  validation reads them.
- A9. **Lifecycle timestamps** for synthetic trips: booked (6 h to 7 days ahead), assigned (21:30
  IST the evening before), en_route, arrived, started, completed; cancelled trips carry
  `cancelled`; no-shows carry `arrived` and `no_show` (15 minutes after the scheduled pickup).
  Future days contain only booked and cancelled trips.
- A10. **Package trips** have a contractual duration of `package_hours`; their pickup equals the
  drop and their actual distance is drawn between 30 and 110 km. They are excluded from ETA
  residual logging.
- A11. **Addresses** are synthetic (`<n> Main Road, <zone>, Bengaluru`) and are the only address
  data shown to drivers.

## Snapshots and ingest

- A12. **Snapshots are deduplicated by content hash.** Ingesting identical content for a date
  reuses the latest snapshot id, so jobs that compare snapshot ids stay idempotent.
- A13. **Trip changes and trip lifecycle are stored separately.** `trip.created`, `trip.updated`
  and `trip.cancelled` events create a new snapshot (parent linked) because they change what the
  planner sees. `trip.status` events (en route, arrived, started, completed, with actuals) update
  `trip_live`, which overlays the snapshot in read models.
- A14. **Ops fleet edits** (vehicle status, driver shift exceptions) are stored as
  `fleet_exception` rows and applied on top of DataSource records at ingest, so the next ingest
  does not silently revert them.
- A15. **Pings are partitioned by day** with a DEFAULT partition; daily partitions on IST day
  boundaries are created ahead of time by the worker.

## Infrastructure

- A16. **OSRM extract**: Geofabrik does not publish a Karnataka-only extract, so `make osrm-data`
  downloads the India southern zone extract, which contains Karnataka.
- A17. **Web served behind nginx** on port 5173 in compose, proxying `/api` to the api service,
  so the browser talks to one origin.
- A18. **Login rate limit** is per client IP per calendar minute (5 attempts).
- A19. **Runtime settings** edited through `PUT /settings` are stored as overrides in the
  database and merged over the environment values. Only buffers, caps, thresholds and provider
  toggles are editable; infrastructure settings stay environment only.
- A20. **Gravity model for synthetic geography.** City drop zones and ETS home zones are drawn
  with weight `exp(-distance / 6 km)` from the pickup zone (ETS: from the tech park). Uniform zone
  pairs made the median ETS trip about 2 hours at P80 under the spec's speed profile.
- A21. **Driver and vehicle pairing is shuffled** (seeded) so every shift has both sedans and SUVs.
- A22. **HERE is disabled in this build container** (egress policy blocks HERE hosts). HERE
  behaviour is verified against recorded response fixtures replayed through respx. With a key
  and network access, `HERE_ENABLED=true` turns it on with no code change.

## ETA

- A23. **Factor bases.** The factor table carries a `basis` column (here, osrm, fallback).
  Residual ratios are actual duration over the provider's raw duration (`eta_log.base_s`) so
  factors do not compound across calibrations. The spec's default 1.00, 1.15, 1.30 applies to here
  and fallback before calibration; OSRM defaults multiply those by the congestion ratio
  (free-flow speed over the bin speed of the speed profile).
- A24. **Trip legs include handling.** Trip leg base durations add HANDLING_S (240 s, pickup and
  drop handling) to the provider duration; calibration then works on the same base.
- A25. **Time bins for factors** are the six speed profile bins (00:00, 06:00, 07:00, 10:00,
  17:00, 21:00); cache keys use 15 minute departure bins as specified.
- A26. **Zone clusters**: k-means (k = 8, seeded k-means++) over H3 resolution 7 cell centroids of
  pickups in eta_log, weighted by trip count, recomputed at each calibration and stored in
  `zone_cluster` with the calibration id.
- A27. **Fallback estimates are persisted for history** but never served from cache, so a provider
  answer replaces them as soon as one is available. Provider rows are never overwritten by fallback.
- A28. **Hub leg pruning**: start and end legs are only estimated for vehicle and trip pairs that
  are eligible and could plausibly fit in the shift at free-flow speed.
- A29. **Seeded history** backfills eta_log by predicting past trips with the offline chain
  (cache, OSRM, fallback) so calibration runs on day one without spending HERE budget.
- A30. **Package trips** use `PACKAGE_KM_PER_HOUR` (12) for planned energy, since they have no
  route.

## Solver and plans

- A31. **Service starts at the scheduled pickup.** The time cumul of a trip node is the scheduled
  pickup time; arriving up to EARLY_ARRIVAL_S early is the planned arrival target, and
  `arrival + buffer <= pickup` is the hard constraint. Modelling the window as the arrival with the
  trip starting at window start would let chains start trips before the customer is ready.
- A32. **Deterministic stopping rule**: the search stops at SOLVER_SOLUTION_LIMIT solutions (200,
  about 25 s on the synthetic day here) with SOLVER_TIME_LIMIT_S as a safety net. Identical inputs
  give identical plans when the solution limit is reached first; `stop_reason` is recorded.
- A33. **SetAllowedVehiclesForIndex fallback**: the OR-Tools 9.15 Python wheel cannot pass a list to
  that method, so the solver restricts the vehicle variable domain instead, which is equivalent.
- A34. **Energy per arc** groups the deadhead into a trip with the trip itself, so solver cumuls
  and the route evaluator agree exactly.
- A35. **Fairness cost** above MAX_TRIPS_PER_DRIVER is FAIRNESS_COST_S (1800) per extra trip.
- A36. **Plan routes table** (`plan_route`) stores each driver's end leg, energy and cap per version
  so later versions can reuse stored legs exactly.
- A37. **Locks and acknowledgements create versions.** Locking or unlocking creates a new version
  (trigger lock or unlock). Unassigned acknowledgements are stored per service date, trip and reason,
  so they carry across versions.
- A38. **Repairs on a published plan are published immediately** (drivers need them now); repairs
  on a draft stay drafts. Ops moves always create drafts that ops publishes.
- A39. **auto_publish acknowledges remaining unassigned trips** on ops' behalf
  (actor `job:auto_publish`) so drivers always receive a plan; the trips stay visible in the tray.
- A40. **Freezing rule for repairs**: a trip is frozen on its driver once it has arrived, started,
  completed or been marked no-show, or when its scheduled pickup time has passed. En-route trips
  can still move. Only freed drivers use live positions and times; locked drivers keep their stored
  plan state so their seeded routes stay feasible.
- A41. **Late prediction never makes a trip unassigned.** If no driver can take the late trip in
  time the repair is recorded as `no_feasible_repair` and the trip stays with its driver.
- A42. **Float drivers** are drivers on shift with no assignment overlapping the next
  FLOAT_HORIZON_S (90 minutes) from now; for repairs the night before, from the affected pickup.
- A43. **Late booking candidates**: up to 4 eligible drivers, feasible insertions first, then by
  distance to the new pickup, plus float drivers.
- A44. **Absence needs a live ping feed.** Drivers are judged absent only when some driver has
  pinged for the date, so a missing driver app feed is not mistaken for 25 absentees.
- A45. **Driver payload** on publish lists, per driver id, the ordered stops with planned pickup
  time and pickup and drop addresses, nothing else.

## Synthetic demand versus capacity

- A46. **The specified synthetic world is over capacity at the peaks.** With the given speed
  profile (15 km/h morning, 13 km/h evening peak), shift pattern and 25 to 26 drivers, about 34
  trips fall in each of the 08:00 to 10:00 and 18:00 to 20:00 windows while about 17 drivers are on
  shift, and a typical 9 km trip takes about 70 minutes at P80. A minimum path cover shows more than
  60 chains would be needed to cover a day. Plans therefore assign about 35 to 40% of trips, and
  every dropped trip carries a reason, which satisfies the acceptance clause "or every dropped trip
  shows a reason". With real demand, measured speeds and calibrated HERE durations the share will
  differ; the replay report is the place to read it.
