# Mobile Shop Management System

A web-based application built using Flask and MySQL to manage mobile inventory, customer accounts, suppliers, and sales logs.

## Features
- **Admin Dashboard:** Monitor inventory, register customers, suppliers, and track revenue.
- **Customer Workspace:** Browse latest mobiles, manage cart, and track order history.
- **Reports & Analytics:** Database-driven summaries for sales and inventory.

## How to Run Locally

1. Install required packages:
   ```bash
   pip install Flask mysql-connector-python werkzeug
   ```

2. Update your MySQL password in `app.py` (Line 34).

3. Run the server:
   ```bash
   python app.py
   ```

4. Open browser and go to: `http://127.0.0.1:5000`
