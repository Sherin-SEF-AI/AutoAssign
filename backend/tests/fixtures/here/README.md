# HERE response fixtures

Responses in the shape documented for HERE Routing v8 (`GET /v8/routes?return=summary`) and
Matrix Routing v8 (`POST /v8/matrix`, sync 200 and async 202 with status polling). Tests replay
them through respx so CI never calls HERE and never spends budget.
