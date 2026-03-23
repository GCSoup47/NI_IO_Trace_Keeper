# NI_IO_Trace_Keeper
Keeps NI I/O Trace running during long tests, restores capture if needed, injects timestamped TRACE_ANCHOR markers, and extracts recent viWrite/viRead activity into a readable failure-focused timeline for debugging.

Scope
1. Launch and monitor NI I/O Trace to keep the application running during long-duration testing.
2. Detect unexpected NI I/O Trace exits and automatically restart the application after a short delay.
3. Verify whether NI I/O Trace capture is enabled and automatically re-enable capture if it is found OFF.
4. Start and maintain an embedded local marker server that listens for anchor traffic on a VISA socket endpoint.
5. Periodically inject TRACE_ANCHOR messages into NI I/O Trace using NI-VISA to create reliable timing reference points.
6. Tag anchor messages with execution timestamp, host machine name, and event type such as startup, restart, or heartbeat.
7. Capture timestamp of when a failure-triggered refracting/parsing script is executed to indicate when the DUT failure occurred.
8. Read the active NI I/O Trace file (for example, dummy.txt) and extract the most recent trace window, such as the last 1000 lines, to preserve relevant pre-failure activity.
9. Locate and extract the most recent TRACE_ANCHOR entry from the recent trace window to obtain a reliable internal timing reference.
10. Parse the anchor into structured fields including timestamp, host, and event for traceability and interpretation.
11. Refract relevant NI I/O Trace entries, including viWrite, viRead, and Formatted viWrite, into a human-readable timeline.
12. Normalize device identifiers and reconstruct timestamps where possible using the extracted anchor and trace context.
13. Include source trace file path, execution metadata, and other available context in the output header for traceability.
14. Output a single human-readable refracted text file containing execution context, anchor details, and parsed recent trace activity.
15. Support failure analysis by combining watchdog-generated anchor infrastructure with a lightweight recent-trace refracting workflow rather than requiring full-log review.

Notes:
- This solution has two functional layers:
  (a) a persistent watchdog that keeps NI I/O Trace alive, keeps capture enabled, and injects periodic anchors, and
  (b) a failure-focused parser/refractor that extracts only recent, relevant trace context.
- TRACE_ANCHOR entries are intentionally generated through a localhost VISA socket path so that later parsing has dependable internal reference markers.
- Heartbeat, startup, and restart anchors improve timing confidence when reconstructing event order around failures.
- The refracted output is intentionally concise and excludes full raw trace files unless deeper investigation is needed.
