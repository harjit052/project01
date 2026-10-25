# Deploy on Vercel

This project uses Vercel's FastAPI runtime. `app.py` serves `new.html` and handles the API; outpass requests are stored in PostgreSQL so they persist between serverless function calls.

1. Import this folder's Git repository into Vercel.
2. Add a PostgreSQL database from the Vercel Marketplace (Neon or another PostgreSQL provider).
3. Add these Production environment variables in Project Settings:
   - `DATABASE_URL`: the database connection URL from the provider.
   - `OUTPASS_ADMIN_PASSWORD`: a strong password for the Admin Console.
4. Deploy or redeploy the project.

The app creates its `outpass_requests` table automatically on the first API request. For local development, install `requirements.txt`, set the same two environment variables, then run `python new.py`.
