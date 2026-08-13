# Frontend

This is a static frontend for the podcast agent application. It sends two separate persona fields as the `personas` array expected by `proxy.py`.

Open `index.html` in your browser, then set:

```text
API base URL: https://YOUR_API_ID.execute-api.eu-north-1.amazonaws.com/YOUR_STAGE
Create job path: /jobs
Status path: /status
```

You can also run it locally:

```bash
cd frontend
python3 -m http.server 8080
```

Then open:

```text
http://127.0.0.1:8080
```

Your API Gateway must allow CORS for the browser. At minimum, allow:

```text
Access-Control-Allow-Origin: *
Access-Control-Allow-Headers: Content-Type
Access-Control-Allow-Methods: GET,POST,OPTIONS
```
