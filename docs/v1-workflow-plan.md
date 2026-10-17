# MetricGuard v1 workflow implementation plan

1. Add import queue records and lease transitions to the metadata store. Test queue, recovery, failure and publication.
2. Connect the API upload to staging in isolated mode, and run imports through the trusted supervisor. Test the HTTP → worker → snapshot path with an in-process runner adapter; retain live Docker as an environment verification gate.
3. Implement deterministic candidate ranking over typed IR and expose it through the API. Test ordering, warnings and review override.
4. Update the dataset, inventory and history screens for import states, suggestions and fixed-snapshot replay. Build and inspect the browser flow.
5. Update contracts and operating instructions; run the full suite and production build.
