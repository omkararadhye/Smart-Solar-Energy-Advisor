# Smart-Solar-Energy-Advisor
A production-style system that simulates and analyzes residential solar energy usage with battery storage, real-time insights, and cost optimization.
🧠 Features
☀️ Solar Generation Modeling (weather-based Gaussian curve)
⚡ Load Prediction (realistic 24-hour residential profile)
🔋 Battery Simulation (charge/discharge with efficiency)
💰 Cost & Savings Analysis (grid vs solar vs export)
⚡ Time-of-Use Tariff System (peak / shoulder / off-peak pricing)
📊 24-hour Forecast API for charts
📈 Historical Energy Data Tracking
🌍 Location-aware system (GPS + timezone support)
🧠 Smart Insight Engine (recommendations based on demand & tariffs)
🔄 Runtime Editable Configuration (including tariffs)
Supports dynamic pricing:
🟥 Peak hours (higher cost)
🟦 Shoulder hours
🟩 Off-peak hours (cheaper)
Automatically adjusts:
Grid cost calculation
Savings estimation
Battery usage strategy
🛠️ Tech Stack
Backend: FastAPI (Python)
Database: SQLite (SQLAlchemy ORM)
Validation: Pydantic
API Docs: Swagger (auto-generated)
🔌 API Highlights
/api/dashboard → Main system state
/api/predict/{hour} → Forecast data
/api/readings → Historical data
/api/tariffs → Tariff management 🔥
/api/location → GPS configuration
/api/efficiency → Solar utilization
/api/battery/charge → Battery control
