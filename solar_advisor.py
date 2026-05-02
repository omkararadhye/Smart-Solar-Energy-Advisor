"""
╔══════════════════════════════════════════════════════════════╗
║          SOLAR ENERGY ADVISOR  —  single-file app            ║
║  Run:  pip install fastapi uvicorn sqlalchemy                 ║
║        python solar_advisor.py                               ║
║  Open: http://localhost:8000   (dashboard UI)                ║
║        http://localhost:8000/docs  (API explorer)            ║
╚══════════════════════════════════════════════════════════════╝
"""

# ──────────────────────────────────────────────────────────────
# 0.  STDLIB / THIRD-PARTY IMPORTS
# ──────────────────────────────────────────────────────────────
import math
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Literal, Generator

import uvicorn
from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import (
    Column, Integer, Float, String, DateTime, Boolean, create_engine
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session


# ──────────────────────────────────────────────────────────────
# 1.  CONFIGURATION  (edit these defaults or set env-vars)
# ──────────────────────────────────────────────────────────────
DATABASE_URL              = os.getenv("DATABASE_URL",            "sqlite:///./solar_advisor.db")
DEFAULT_GRID_PRICE        = float(os.getenv("GRID_PRICE",        "8.50"))   # INR / kWh
DEFAULT_EXPORT_PRICE      = float(os.getenv("EXPORT_PRICE",      "3.50"))   # INR / kWh
DEFAULT_CO2_FACTOR        = float(os.getenv("CO2_FACTOR",        "0.82"))   # kg CO2 / kWh
DEFAULT_SOLAR_CAPACITY_KW = float(os.getenv("SOLAR_CAPACITY_KW", "10.0"))   # kWp
DEFAULT_BATTERY_KWH       = float(os.getenv("BATTERY_KWH",       "13.5"))   # kWh
DEFAULT_BATTERY_EFF       = float(os.getenv("BATTERY_EFF",       "0.92"))   # 0-1
APP_VERSION               = "2.0.0"


# ──────────────────────────────────────────────────────────────
# 2.  DATABASE  (SQLAlchemy)
# ──────────────────────────────────────────────────────────────
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── ORM Models ────────────────────────────────────────────────

class SystemSettings(Base):
    __tablename__ = "system_settings"
    id                    = Column(Integer, primary_key=True, index=True)
    system_id             = Column(String(64), unique=True, index=True, default="default")
    grid_price_per_kwh    = Column(Float,  nullable=False)
    export_price_per_kwh  = Column(Float,  nullable=False)
    co2_factor            = Column(Float,  nullable=False)
    max_solar_capacity_kw = Column(Float,  nullable=False)
    battery_capacity_kwh  = Column(Float,  nullable=False)
    battery_efficiency    = Column(Float,  nullable=False)
    # ── NEW: location fields ──
    city                  = Column(String(128), default="")
    country               = Column(String(64),  default="")
    latitude              = Column(Float,        default=0.0)
    longitude             = Column(Float,        default=0.0)
    timezone_offset_hrs   = Column(Float,        default=5.5)   # IST default
    updated_at            = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by            = Column(String(64),  default="system")


class BatteryState(Base):
    __tablename__ = "battery_state"
    id           = Column(Integer, primary_key=True, index=True)
    system_id    = Column(String(64), unique=True, index=True, default="default")
    charge_kwh   = Column(Float,  default=0.0)
    soc_percent  = Column(Float,  default=0.0)
    is_charging  = Column(Boolean, default=False)
    last_updated = Column(DateTime(timezone=True), default=utcnow)


class EnergyReading(Base):
    __tablename__ = "energy_readings"
    id               = Column(Integer, primary_key=True, index=True)
    system_id        = Column(String(64), index=True, default="default")
    timestamp        = Column(DateTime(timezone=True), default=utcnow, index=True)
    load_w           = Column(Float)
    solar_w          = Column(Float)
    grid_w           = Column(Float)
    battery_w        = Column(Float, default=0.0)
    weather_condition = Column(String(32), default="clear")
    tariff_slot      = Column(String(32), default="off-peak")
    efficiency_score = Column(Float)
    savings_inr      = Column(Float, default=0.0)


class TariffSchedule(Base):
    __tablename__ = "tariff_schedule"
    id               = Column(Integer, primary_key=True, index=True)
    system_id        = Column(String(64), index=True, default="default")
    slot_name        = Column(String(32))
    hour_start       = Column(Integer)
    hour_end         = Column(Integer)
    price_multiplier = Column(Float, default=1.0)
    is_active        = Column(Boolean, default=True)


Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────
# 3.  REPOSITORIES  (data access layer)
# ──────────────────────────────────────────────────────────────

class SettingsRepo:
    @staticmethod
    def get(db: Session, system_id: str = "default") -> SystemSettings:
        row = db.query(SystemSettings).filter_by(system_id=system_id).first()
        if row is None:
            row = SystemSettings(
                system_id=system_id,
                grid_price_per_kwh=DEFAULT_GRID_PRICE,
                export_price_per_kwh=DEFAULT_EXPORT_PRICE,
                co2_factor=DEFAULT_CO2_FACTOR,
                max_solar_capacity_kw=DEFAULT_SOLAR_CAPACITY_KW,
                battery_capacity_kwh=DEFAULT_BATTERY_KWH,
                battery_efficiency=DEFAULT_BATTERY_EFF,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    @staticmethod
    def update(db: Session, data: dict, system_id: str = "default") -> SystemSettings:
        row = SettingsRepo.get(db, system_id)
        for k, v in data.items():
            if v is not None and hasattr(row, k):
                setattr(row, k, v)
        row.updated_at = utcnow()
        db.commit()
        db.refresh(row)
        return row


class BatteryRepo:
    @staticmethod
    def get(db: Session, system_id: str = "default") -> BatteryState:
        row = db.query(BatteryState).filter_by(system_id=system_id).first()
        if row is None:
            row = BatteryState(system_id=system_id)
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    @staticmethod
    def update_charge(db: Session, delta_kwh: float, settings: SystemSettings, system_id: str = "default") -> BatteryState:
        row = BatteryRepo.get(db, system_id)
        cap = settings.battery_capacity_kwh
        actual = delta_kwh * settings.battery_efficiency if delta_kwh > 0 else delta_kwh
        row.charge_kwh  = round(max(0.0, min(cap, row.charge_kwh + actual)), 3)
        row.soc_percent = round((row.charge_kwh / cap) * 100, 1) if cap else 0
        row.is_charging = delta_kwh > 0
        row.last_updated = utcnow()
        db.commit()
        db.refresh(row)
        return row


class TariffRepo:
    @staticmethod
    def get_all(db: Session, system_id: str = "default") -> list:
        rows = db.query(TariffSchedule).filter_by(system_id=system_id, is_active=True).order_by(TariffSchedule.hour_start).all()
        if not rows:
            defaults = [
                TariffSchedule(system_id=system_id, slot_name="off-peak",  hour_start=0,  hour_end=5,  price_multiplier=0.70),
                TariffSchedule(system_id=system_id, slot_name="shoulder",  hour_start=6,  hour_end=9,  price_multiplier=1.00),
                TariffSchedule(system_id=system_id, slot_name="off-peak",  hour_start=10, hour_end=17, price_multiplier=0.85),
                TariffSchedule(system_id=system_id, slot_name="peak",      hour_start=18, hour_end=22, price_multiplier=1.50),
                TariffSchedule(system_id=system_id, slot_name="shoulder",  hour_start=23, hour_end=23, price_multiplier=1.00),
            ]
            for d in defaults:
                db.add(d)
            db.commit()
            rows = defaults
        return rows

    @staticmethod
    def get_slot_for_hour(db: Session, hour: int, system_id: str = "default"):
        for slot in TariffRepo.get_all(db, system_id):
            if slot.hour_start <= hour <= slot.hour_end:
                return slot
        return None

    @staticmethod
    def create(db: Session, data: dict) -> TariffSchedule:
        row = TariffSchedule(**data)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    @staticmethod
    def delete(db: Session, slot_id: int) -> bool:
        row = db.query(TariffSchedule).filter_by(id=slot_id).first()
        if not row:
            return False
        row.is_active = False
        db.commit()
        return True


class ReadingsRepo:
    @staticmethod
    def save(db: Session, reading: EnergyReading) -> EnergyReading:
        db.add(reading)
        db.commit()
        db.refresh(reading)
        return reading

    @staticmethod
    def recent(db: Session, system_id: str = "default", hours: int = 24) -> list:
        since = utcnow() - timedelta(hours=hours)
        return (
            db.query(EnergyReading)
            .filter(EnergyReading.system_id == system_id, EnergyReading.timestamp >= since)
            .order_by(EnergyReading.timestamp.desc())
            .all()
        )


# ──────────────────────────────────────────────────────────────
# 4.  PYDANTIC SCHEMAS  (request / response validation)
# ──────────────────────────────────────────────────────────────

WeatherCondition = Literal["clear", "partly_cloudy", "overcast", "rainy", "foggy", "stormy"]

WEATHER_DERATING: dict[str, float] = {
    "clear": 1.00, "partly_cloudy": 0.65, "overcast": 0.30,
    "rainy": 0.15, "foggy": 0.20,        "stormy": 0.05,
}


class SettingsUpdateSchema(BaseModel):
    grid_price_per_kwh:    Optional[float] = Field(None, gt=0)
    export_price_per_kwh:  Optional[float] = Field(None, gt=0)
    co2_factor:            Optional[float] = Field(None, gt=0)
    max_solar_capacity_kw: Optional[float] = Field(None, gt=0)
    battery_capacity_kwh:  Optional[float] = Field(None, ge=0)
    battery_efficiency:    Optional[float] = Field(None, gt=0, le=1.0)
    # Location fields
    city:                  Optional[str]   = Field(None, max_length=128)
    country:               Optional[str]   = Field(None, max_length=64)
    latitude:              Optional[float] = Field(None, ge=-90,  le=90)
    longitude:             Optional[float] = Field(None, ge=-180, le=180)
    timezone_offset_hrs:   Optional[float] = Field(None, ge=-12, le=14)
    updated_by:            str = "admin"


class LocationSchema(BaseModel):
    """Lightweight schema just for updating location from the browser."""
    city:                str   = ""
    country:             str   = ""
    latitude:            float = 0.0
    longitude:           float = 0.0
    timezone_offset_hrs: float = 5.5
    system_id:           str   = "default"


class BatteryChargeSchema(BaseModel):
    delta_kwh: float = Field(..., description="+ve = charge, -ve = discharge")


class TariffSlotSchema(BaseModel):
    slot_name:        str   = Field(..., max_length=32)
    hour_start:       int   = Field(..., ge=0, le=23)
    hour_end:         int   = Field(..., ge=0, le=23)
    price_multiplier: float = Field(..., gt=0)
    system_id:        str   = "default"


# ──────────────────────────────────────────────────────────────
# 5.  BUSINESS LOGIC / SERVICES
# ──────────────────────────────────────────────────────────────

def _local_hour(settings: SystemSettings) -> int:
    """Return current hour adjusted for the system's stored timezone offset."""
    utc_now = datetime.now(timezone.utc)
    local_dt = utc_now + timedelta(hours=settings.timezone_offset_hrs)
    return local_dt.hour


def calculate_solar(hour: int, weather: WeatherCondition, max_kw: float) -> tuple[float, str]:
    """Bell-curve solar model centred on solar noon."""
    if hour < 6 or hour > 19:
        return 0.0, "none"
    raw      = math.exp(-0.5 * ((hour - 13) / 5.5) ** 2)   # Gaussian, peak at 13:00
    solar_w  = round(raw * max_kw * 1000 * WEATHER_DERATING.get(weather, 1.0), 1)
    status   = "excellent" if solar_w > max_kw * 700 else "good" if solar_w > max_kw * 400 else "moderate"
    return solar_w, status


def calculate_load(hour: int, weather: WeatherCondition) -> tuple[float, str]:
    """Typical Indian residential 24-h load profile."""
    PROFILE = {
        0:0.30,1:0.25,2:0.22,3:0.20,4:0.20,5:0.22,
        6:0.45,7:0.65,8:0.70,9:0.60,10:0.55,11:0.50,
        12:0.55,13:0.55,14:0.50,15:0.52,16:0.58,17:0.75,
        18:0.90,19:1.00,20:0.95,21:0.85,22:0.70,23:0.50,
    }
    load_w = PROFILE.get(hour, 0.5) * 2000
    if weather in ("clear", "partly_cloudy") and 12 <= hour <= 20:
        load_w += 400 if weather == "clear" else 200   # AC load
    load_w = round(load_w, 1)
    status = "high" if load_w > 1800 else "medium" if load_w > 1000 else "low"
    return load_w, status


def get_tariff_info(slot, base_price: float) -> tuple[str, float, float]:
    if slot is None:
        return "off-peak", 1.0, base_price
    return slot.slot_name, slot.price_multiplier, round(base_price * slot.price_multiplier, 4)


def calc_economics(load_w, solar_w, soc_pct, settings, eff_price) -> dict:
    cap      = settings.battery_capacity_kwh
    batt_kwh = (soc_pct / 100) * cap
    net_w    = load_w - solar_w

    if net_w <= 0:                              # surplus solar
        surplus_w    = abs(net_w)
        charge_kwh   = min(surplus_w / 1000, cap - batt_kwh)
        export_w     = surplus_w - charge_kwh * 1000
        grid_w       = -export_w
        batt_delta   = charge_kwh
    else:                                       # deficit
        discharge_w  = min(net_w, batt_kwh * 1000)
        grid_w       = max(0.0, net_w - discharge_w)
        batt_delta   = -(discharge_w / 1000)

    import_kwh   = max(0.0,  grid_w) / 1000
    export_kwh   = max(0.0, -grid_w) / 1000
    cost_inr     = import_kwh * eff_price
    baseline_inr = load_w / 1000 * eff_price
    savings_inr  = baseline_inr - cost_inr + export_kwh * settings.export_price_per_kwh
    co2_saved    = (solar_w / 1000) * settings.co2_factor

    return {
        "grid_w":        round(grid_w,      1),
        "battery_delta": round(batt_delta,  4),
        "battery_w":     round(batt_delta * 1000, 1),
        "cost_inr":      round(cost_inr,    2),
        "savings_inr":   round(savings_inr, 2),
        "co2_saved_kg":  round(co2_saved,   4),
    }


def eff_score(solar_w: float, load_w: float) -> float:
    if load_w <= 0:
        return 100.0
    return round(min(solar_w, load_w) / load_w * 100, 1)


def eff_level(score: float) -> str:
    return "Excellent" if score >= 85 else "Good" if score >= 60 else "Moderate" if score >= 35 else "Poor"


def build_insight(load_w, solar_w, soc, tariff_slot, weather) -> dict:
    if tariff_slot == "peak" and soc > 30:
        return {"message":"⚡ Peak tariff — discharging battery","action":"discharge_battery","priority":"high",
                "tips":["Avoid washer/AC/oven during peak hours."]}
    if solar_w > load_w * 1.2 and soc < 80:
        return {"message":"☀️ Surplus solar — charging battery","action":"charge_battery","priority":"low",
                "tips":["Great time to run heavy appliances."]}
    if solar_w > load_w and soc >= 80:
        return {"message":"☀️ Battery full — exporting to grid","action":"export_to_grid","priority":"low",
                "tips":["Earning feed-in revenue. No action needed."]}
    if load_w > 1600 and solar_w < 200 and soc < 20:
        return {"message":"⚠️ High demand + low solar + low battery","action":"reduce_load","priority":"high",
                "tips":["Defer non-essential loads.","Shift EV charging to off-peak."]}
    if weather in ("rainy","stormy","overcast"):
        return {"message":f"🌧️ Low solar ({weather}) — grid is primary","action":"monitor","priority":"medium",
                "tips":["Keep battery above 30% as backup."]}
    return {"message":"✅ System running normally","action":"monitor","priority":"low",
            "tips":["Solar is covering a good portion of demand."]}


def build_dashboard_data(hour, weather, settings, battery, tariff_slot, system_id="default") -> dict:
    solar_w,  solar_status = calculate_solar(hour, weather, settings.max_solar_capacity_kw)
    load_w,   load_status  = calculate_load(hour, weather)
    slot_name, multiplier, eff_price = get_tariff_info(tariff_slot, settings.grid_price_per_kwh)
    econ = calc_economics(load_w, solar_w, battery.soc_percent, settings, eff_price)
    score = eff_score(solar_w, load_w)

    return {
        "system_id": system_id,
        "timestamp": utcnow().isoformat(),
        "hour": hour,
        "weather": weather,
        "location": {
            "city": settings.city,
            "country": settings.country,
            "latitude": settings.latitude,
            "longitude": settings.longitude,
            "timezone_offset_hrs": settings.timezone_offset_hrs,
        },
        "load_w": load_w, "load_kw": round(load_w/1000, 3), "load_status": load_status,
        "solar_w": solar_w, "solar_kw": round(solar_w/1000, 3), "solar_status": solar_status,
        "grid_w": econ["grid_w"], "battery_w": econ["battery_w"],
        "tariff_slot": slot_name, "tariff_multiplier": multiplier,
        "battery_soc_percent": battery.soc_percent, "battery_charge_kwh": battery.charge_kwh,
        "grid_price_per_kwh": settings.grid_price_per_kwh,
        "current_tariff_price": eff_price,
        "hourly_cost_inr": econ["cost_inr"],
        "hourly_savings_inr": econ["savings_inr"],
        "co2_saved_kg": econ["co2_saved_kg"],
        "efficiency_score": score,
        "efficiency_level": eff_level(score),
        "fill_factor": round(solar_w / (settings.max_solar_capacity_kw * 1000), 3),
        "insight": build_insight(load_w, solar_w, battery.soc_percent, slot_name, weather),
    }


# ──────────────────────────────────────────────────────────────
# 6.  FASTAPI APP + ROUTES
# ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Solar Energy Advisor API",
    version=APP_VERSION,
    description="Production-grade solar + battery advisor. All config is runtime-editable.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── helpers ───────────────────────────────────────────────────
def _hour_param(hour: Optional[int] = Query(None, ge=0, le=23)) -> int:
    return hour if hour is not None else datetime.now().hour


# ── Health ────────────────────────────────────────────────────
@app.get("/api/health", tags=["System"])
def health():
    return {"status": "ok", "version": APP_VERSION, "time": utcnow().isoformat()}


# ── Settings ─────────────────────────────────────────────────
@app.get("/api/settings", tags=["Admin"])
def get_settings(system_id: str = Query("default"), db: Session = Depends(get_db)):
    row = SettingsRepo.get(db, system_id)
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


@app.put("/api/settings", tags=["Admin"])
def update_settings(
    payload: SettingsUpdateSchema,
    system_id: str = Query("default"),
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude_none=True)
    row  = SettingsRepo.update(db, data, system_id)
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


# ── Location (dedicated endpoint for easy frontend use) ───────
@app.post("/api/location", tags=["Location"])
def set_location(payload: LocationSchema, db: Session = Depends(get_db)):
    """
    Save the user's location (from browser geolocation or manual entry).
    Frontend can call this once on load, then all calculations use local time.
    """
    data = payload.model_dump(exclude={"system_id"})
    row  = SettingsRepo.update(db, data, payload.system_id)
    return {
        "message": "Location saved",
        "city": row.city,
        "country": row.country,
        "latitude": row.latitude,
        "longitude": row.longitude,
        "timezone_offset_hrs": row.timezone_offset_hrs,
    }


# ── Dashboard ─────────────────────────────────────────────────
@app.get("/api/dashboard", tags=["Energy"])
def dashboard(
    hour:      Optional[int]    = Query(None, ge=0, le=23),
    weather:   WeatherCondition = Query("clear"),
    system_id: str              = Query("default"),
    db: Session = Depends(get_db),
):
    settings    = SettingsRepo.get(db, system_id)
    battery     = BatteryRepo.get(db, system_id)
    actual_hour = hour if hour is not None else _local_hour(settings)
    tariff_slot = TariffRepo.get_slot_for_hour(db, actual_hour, system_id)

    data = build_dashboard_data(actual_hour, weather, settings, battery, tariff_slot, system_id)

    # Persist reading
    ReadingsRepo.save(db, EnergyReading(
        system_id=system_id, load_w=data["load_w"], solar_w=data["solar_w"],
        grid_w=data["grid_w"], battery_w=data["battery_w"],
        weather_condition=weather, tariff_slot=data["tariff_slot"],
        efficiency_score=data["efficiency_score"], savings_inr=data["hourly_savings_inr"],
    ))
    return data


# ── Solar / Load ──────────────────────────────────────────────
@app.get("/api/solar", tags=["Energy"])
def solar(
    hour:      int             = Depends(_hour_param),
    weather:   WeatherCondition = Query("clear"),
    system_id: str             = Query("default"),
    db: Session = Depends(get_db),
):
    s = SettingsRepo.get(db, system_id)
    w, status = calculate_solar(hour, weather, s.max_solar_capacity_kw)
    return {"hour": hour, "weather": weather, "solar_w": w, "solar_kw": round(w/1000,3), "status": status}


@app.get("/api/load", tags=["Energy"])
def load(
    hour:    int             = Depends(_hour_param),
    weather: WeatherCondition = Query("clear"),
):
    w, status = calculate_load(hour, weather)
    return {"hour": hour, "weather": weather, "load_w": w, "load_kw": round(w/1000,3), "status": status}


# ── Insights / Efficiency ─────────────────────────────────────
@app.get("/api/insights", tags=["Energy"])
def insights(
    hour:      int             = Depends(_hour_param),
    weather:   WeatherCondition = Query("clear"),
    system_id: str             = Query("default"),
    db: Session = Depends(get_db),
):
    s       = SettingsRepo.get(db, system_id)
    battery = BatteryRepo.get(db, system_id)
    tariff  = TariffRepo.get_slot_for_hour(db, hour, system_id)
    solar_w, _ = calculate_solar(hour, weather, s.max_solar_capacity_kw)
    load_w,  _ = calculate_load(hour, weather)
    slot_name  = tariff.slot_name if tariff else "off-peak"
    return build_insight(load_w, solar_w, battery.soc_percent, slot_name, weather)


@app.get("/api/efficiency", tags=["Energy"])
def efficiency(
    hour:      int             = Depends(_hour_param),
    weather:   WeatherCondition = Query("clear"),
    system_id: str             = Query("default"),
    db: Session = Depends(get_db),
):
    s = SettingsRepo.get(db, system_id)
    solar_w, _ = calculate_solar(hour, weather, s.max_solar_capacity_kw)
    load_w,  _ = calculate_load(hour, weather)
    score = eff_score(solar_w, load_w)
    return {"efficiency_score": score, "level": eff_level(score), "solar_w": solar_w, "load_w": load_w}


# ── Predict any hour ──────────────────────────────────────────
@app.get("/api/predict/{target_hour}", tags=["Energy"])
def predict(
    target_hour: int,
    weather:   WeatherCondition = Query("clear"),
    system_id: str             = Query("default"),
    db: Session = Depends(get_db),
):
    s      = SettingsRepo.get(db, system_id)
    tariff = TariffRepo.get_slot_for_hour(db, target_hour, system_id)
    solar_w, _ = calculate_solar(target_hour, weather, s.max_solar_capacity_kw)
    load_w,  _ = calculate_load(target_hour, weather)
    slot_name, _, _ = get_tariff_info(tariff, s.grid_price_per_kwh)
    return {
        "hour": target_hour, "weather": weather,
        "solar_w": solar_w, "load_w": load_w,
        "grid_w": round(load_w - solar_w, 1),
        "tariff_slot": slot_name,
    }


# ── Battery ───────────────────────────────────────────────────
@app.get("/api/battery", tags=["Battery"])
def battery_state(system_id: str = Query("default"), db: Session = Depends(get_db)):
    row = BatteryRepo.get(db, system_id)
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


@app.put("/api/battery/charge", tags=["Battery"])
def charge_battery(
    payload:   BatteryChargeSchema,
    system_id: str = Query("default"),
    db: Session = Depends(get_db),
):
    s   = SettingsRepo.get(db, system_id)
    row = BatteryRepo.update_charge(db, payload.delta_kwh, s, system_id)
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


# ── Tariffs ───────────────────────────────────────────────────
@app.get("/api/tariffs", tags=["Admin"])
def list_tariffs(system_id: str = Query("default"), db: Session = Depends(get_db)):
    return TariffRepo.get_all(db, system_id)


@app.post("/api/tariffs", tags=["Admin"], status_code=201)
def create_tariff(payload: TariffSlotSchema, db: Session = Depends(get_db)):
    return TariffRepo.create(db, payload.model_dump())


@app.delete("/api/tariffs/{slot_id}", tags=["Admin"], status_code=204)
def delete_tariff(slot_id: int, db: Session = Depends(get_db)):
    if not TariffRepo.delete(db, slot_id):
        raise HTTPException(404, "Tariff slot not found")


# ── Historical Readings ───────────────────────────────────────
@app.get("/api/readings", tags=["Energy"])
def readings(
    system_id: str = Query("default"),
    hours:     int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db),
):
    rows = ReadingsRepo.recent(db, system_id, hours)
    return {
        "system_id": system_id, "count": len(rows),
        "readings": [{
            "timestamp": r.timestamp, "load_w": r.load_w, "solar_w": r.solar_w,
            "grid_w": r.grid_w, "battery_w": r.battery_w,
            "efficiency_score": r.efficiency_score, "savings_inr": r.savings_inr,
            "weather": r.weather_condition, "tariff_slot": r.tariff_slot,
        } for r in rows],
    }


# ──────────────────────────────────────────────────────────────
# 7.  BUILT-IN DASHBOARD UI  (served at  http://localhost:8000)
# ──────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Solar Energy Advisor</title>
<style>
  :root{--sun:#f59e0b;--green:#10b981;--blue:#3b82f6;--red:#ef4444;--purple:#8b5cf6;--bg:#0f172a;--card:#1e293b;--border:#334155;--text:#e2e8f0;--muted:#94a3b8}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',sans-serif;min-height:100vh}
  header{background:linear-gradient(135deg,#1e293b,#0f172a);padding:1.2rem 2rem;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border)}
  header h1{font-size:1.4rem;color:var(--sun)}
  header span{color:var(--muted);font-size:.85rem}
  .container{max-width:1200px;margin:0 auto;padding:1.5rem}
  /* location bar */
  .loc-bar{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:1rem 1.4rem;margin-bottom:1.5rem;display:flex;flex-wrap:wrap;gap:.8rem;align-items:flex-end}
  .loc-bar h3{width:100%;font-size:.9rem;color:var(--muted);margin-bottom:.2rem}
  .loc-bar input,.loc-bar select{background:#0f172a;border:1px solid var(--border);color:var(--text);padding:.45rem .8rem;border-radius:8px;font-size:.85rem;width:160px}
  .loc-bar input[type=number]{width:110px}
  .btn{background:var(--blue);color:#fff;border:none;padding:.5rem 1.2rem;border-radius:8px;cursor:pointer;font-size:.85rem;font-weight:600;transition:.2s}
  .btn:hover{opacity:.85}
  .btn-geo{background:var(--green)}
  .btn-refresh{background:var(--purple)}
  .loc-status{font-size:.8rem;color:var(--green);align-self:center}
  /* cards */
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1rem;margin-bottom:1.5rem}
  .card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:1.2rem;position:relative;overflow:hidden}
  .card h4{font-size:.78rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:.5rem}
  .card .value{font-size:2rem;font-weight:700;line-height:1}
  .card .unit{font-size:.8rem;color:var(--muted);margin-top:.3rem}
  .card .badge{position:absolute;top:.8rem;right:.8rem;font-size:.7rem;padding:.2rem .6rem;border-radius:20px;font-weight:600}
  .badge-high{background:#fef2f2;color:#dc2626}.badge-medium{background:#fefce8;color:#d97706}.badge-low{background:#f0fdf4;color:#16a34a}
  .badge-excellent{background:#ecfdf5;color:#059669}.badge-good{background:#eff6ff;color:#2563eb}.badge-moderate{background:#fefce8;color:#ca8a04}
  .badge-peak{background:#fef2f2;color:#dc2626}.badge-off-peak{background:#f0fdf4;color:#16a34a}.badge-shoulder{background:#eff6ff;color:#2563eb}
  /* insight */
  .insight-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:1.3rem;margin-bottom:1.5rem}
  .insight-card h3{font-size:.8rem;color:var(--muted);text-transform:uppercase;margin-bottom:.6rem}
  .insight-msg{font-size:1.1rem;font-weight:600;margin-bottom:.5rem}
  .tips{list-style:none;padding:0}
  .tips li{font-size:.85rem;color:var(--muted);padding:.25rem 0;padding-left:1.2rem;position:relative}
  .tips li::before{content:"→";position:absolute;left:0;color:var(--sun)}
  /* battery bar */
  .batt-bar{height:10px;background:#334155;border-radius:6px;margin:.5rem 0;overflow:hidden}
  .batt-fill{height:100%;border-radius:6px;transition:width .5s}
  /* controls */
  .controls{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:1.2rem;margin-bottom:1.5rem;display:flex;flex-wrap:wrap;gap:1rem;align-items:flex-end}
  .controls label{font-size:.8rem;color:var(--muted);display:block;margin-bottom:.3rem}
  .controls select,.controls input{background:#0f172a;border:1px solid var(--border);color:var(--text);padding:.45rem .8rem;border-radius:8px;font-size:.85rem}
  /* chart */
  .chart-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:1.2rem;margin-bottom:1.5rem}
  .chart-card h3{font-size:.8rem;color:var(--muted);text-transform:uppercase;margin-bottom:1rem}
  .bar-chart{display:flex;gap:4px;height:120px;align-items:flex-end}
  .bar-wrap{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px}
  .bar-group{width:100%;display:flex;gap:2px;align-items:flex-end;height:100px}
  .bar{flex:1;border-radius:4px 4px 0 0;min-height:2px;transition:height .4s}
  .bar-label{font-size:.6rem;color:var(--muted)}
  /* footer */
  footer{text-align:center;padding:1rem;color:var(--muted);font-size:.75rem;border-top:1px solid var(--border)}
  .spinner{display:inline-block;width:16px;height:16px;border:2px solid var(--muted);border-top-color:var(--blue);border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle;margin-right:6px}
  @keyframes spin{to{transform:rotate(360deg)}}
  .loading-overlay{position:fixed;inset:0;background:rgba(15,23,42,.7);display:flex;align-items:center;justify-content:center;z-index:99;display:none}
  .loading-overlay.show{display:flex}
  .loading-box{background:var(--card);padding:1.5rem 2rem;border-radius:14px;font-size:1rem}
</style>
</head>
<body>

<div class="loading-overlay" id="loader"><div class="loading-box"><span class="spinner"></span>Loading...</div></div>

<header>
  <h1>☀️ Solar Energy Advisor</h1>
  <span id="header-loc">No location set</span>
</header>

<div class="container">

  <!-- ── LOCATION BAR ── -->
  <div class="loc-bar">
    <h3>📍 Your Location — used for local time & timezone-aware calculations</h3>
    <div>
      <label style="font-size:.75rem;color:var(--muted)">City</label>
      <input id="inp-city" type="text" placeholder="e.g. Pune">
    </div>
    <div>
      <label style="font-size:.75rem;color:var(--muted)">Country</label>
      <input id="inp-country" type="text" placeholder="e.g. India">
    </div>
    <div>
      <label style="font-size:.75rem;color:var(--muted)">Latitude</label>
      <input id="inp-lat" type="number" step="0.0001" placeholder="18.5204">
    </div>
    <div>
      <label style="font-size:.75rem;color:var(--muted)">Longitude</label>
      <input id="inp-lon" type="number" step="0.0001" placeholder="73.8567">
    </div>
    <div>
      <label style="font-size:.75rem;color:var(--muted)">UTC Offset (hrs)</label>
      <input id="inp-tz" type="number" step="0.5" placeholder="5.5">
    </div>
    <button class="btn btn-geo" onclick="useGeolocation()">📡 Auto-detect</button>
    <button class="btn" onclick="saveLocation()">💾 Save Location</button>
    <span id="loc-status" class="loc-status"></span>
  </div>

  <!-- ── SIMULATION CONTROLS ── -->
  <div class="controls">
    <div>
      <label>Hour (0–23, blank = local now)</label>
      <input type="number" id="ctrl-hour" min="0" max="23" placeholder="auto">
    </div>
    <div>
      <label>Weather Condition</label>
      <select id="ctrl-weather">
        <option value="clear">☀️ Clear</option>
        <option value="partly_cloudy">⛅ Partly Cloudy</option>
        <option value="overcast">☁️ Overcast</option>
        <option value="rainy">🌧️ Rainy</option>
        <option value="foggy">🌫️ Foggy</option>
        <option value="stormy">⛈️ Stormy</option>
      </select>
    </div>
    <button class="btn btn-refresh" onclick="refresh()">🔄 Refresh Dashboard</button>
  </div>

  <!-- ── METRIC CARDS ── -->
  <div class="grid" id="cards">
    <div class="card"><h4>Solar Generation</h4><div class="value" id="c-solar">—</div><div class="unit">Watts</div></div>
    <div class="card"><h4>Home Load</h4><div class="value" id="c-load">—</div><div class="unit">Watts</div></div>
    <div class="card"><h4>Grid Usage</h4><div class="value" id="c-grid">—</div><div class="unit">W (+ import / − export)</div></div>
    <div class="card"><h4>Efficiency Score</h4><div class="value" id="c-eff">—</div><div class="unit" id="c-eff-level">—</div></div>
    <div class="card"><h4>Hourly Cost</h4><div class="value" id="c-cost">—</div><div class="unit">INR</div></div>
    <div class="card"><h4>Hourly Savings</h4><div class="value" id="c-save">—</div><div class="unit">INR vs grid-only</div></div>
    <div class="card"><h4>CO₂ Saved</h4><div class="value" id="c-co2">—</div><div class="unit">kg this hour</div></div>
    <div class="card">
      <h4>Battery SOC</h4>
      <div class="value" id="c-soc">—</div>
      <div class="batt-bar"><div class="batt-fill" id="batt-fill" style="width:0%;background:var(--green)"></div></div>
      <div class="unit" id="c-soc-kwh">—</div>
    </div>
  </div>

  <!-- ── INSIGHT ── -->
  <div class="insight-card">
    <h3>💡 Smart Insight</h3>
    <div class="insight-msg" id="ins-msg">Loading...</div>
    <ul class="tips" id="ins-tips"></ul>
  </div>

  <!-- ── 24H FORECAST CHART ── -->
  <div class="chart-card">
    <h3>24-Hour Forecast (current weather condition)</h3>
    <div class="bar-chart" id="bar-chart"></div>
    <div style="display:flex;gap:1.5rem;margin-top:.5rem;font-size:.75rem">
      <span><span style="display:inline-block;width:10px;height:10px;background:var(--sun);border-radius:2px;margin-right:4px"></span>Solar W</span>
      <span><span style="display:inline-block;width:10px;height:10px;background:var(--blue);border-radius:2px;margin-right:4px"></span>Load W</span>
    </div>
  </div>

</div>

<footer>Solar Energy Advisor v2.0 · <a href="/docs" style="color:var(--blue)">API Docs</a></footer>

<script>
const BASE = '';

async function api(path) {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function postApi(path, body) {
  const r = await fetch(BASE + path, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function show(id, val) { document.getElementById(id).textContent = val; }

function badge(text, cls) {
  return `<span class="badge badge-${cls}">${text}</span>`;
}

function battColor(pct) {
  if (pct > 60) return 'var(--green)';
  if (pct > 25) return 'var(--sun)';
  return 'var(--red)';
}

function buildQuery() {
  const h = document.getElementById('ctrl-hour').value.trim();
  const w = document.getElementById('ctrl-weather').value;
  let q = `?weather=${w}`;
  if (h !== '') q += `&hour=${h}`;
  return q;
}

async function refresh() {
  document.getElementById('loader').classList.add('show');
  try {
    const q   = buildQuery();
    const d   = await api('/api/dashboard' + q);

    show('c-solar', d.solar_w.toFixed(0));
    show('c-load',  d.load_w.toFixed(0));
    show('c-grid',  (d.grid_w >= 0 ? '+' : '') + d.grid_w.toFixed(0));
    show('c-eff',   d.efficiency_score.toFixed(1) + '%');
    show('c-eff-level', d.efficiency_level);
    show('c-cost',  '₹' + d.hourly_cost_inr.toFixed(2));
    show('c-save',  '₹' + d.hourly_savings_inr.toFixed(2));
    show('c-co2',   d.co2_saved_kg.toFixed(3));
    show('c-soc',   d.battery_soc_percent.toFixed(1) + '%');
    show('c-soc-kwh', d.battery_charge_kwh.toFixed(2) + ' kWh stored');

    const fill = document.getElementById('batt-fill');
    fill.style.width     = d.battery_soc_percent + '%';
    fill.style.background = battColor(d.battery_soc_percent);

    // Insight
    show('ins-msg', d.insight.message);
    const tipsList = document.getElementById('ins-tips');
    tipsList.innerHTML = d.insight.tips.map(t => `<li>${t}</li>`).join('');

    // Location header
    const loc = d.location;
    const locStr = loc.city ? `📍 ${loc.city}, ${loc.country} (UTC${loc.timezone_offset_hrs >= 0 ? '+' : ''}${loc.timezone_offset_hrs})` : 'No location set';
    show('header-loc', locStr);

    // 24h chart
    await renderChart();
  } catch(e) {
    alert('Error: ' + e.message);
  } finally {
    document.getElementById('loader').classList.remove('show');
  }
}

async function renderChart() {
  const weather = document.getElementById('ctrl-weather').value;
  const chart   = document.getElementById('bar-chart');
  chart.innerHTML = '<span class="spinner"></span>';

  const hours   = Array.from({length: 24}, (_, i) => i);
  const results = await Promise.all(hours.map(h => api(`/api/predict/${h}?weather=${weather}`)));

  const maxW = Math.max(...results.map(r => Math.max(r.solar_w, r.load_w)), 1);
  chart.innerHTML = results.map((r, h) => `
    <div class="bar-wrap">
      <div class="bar-group">
        <div class="bar" style="height:${(r.solar_w/maxW*100).toFixed(1)}%;background:var(--sun)" title="Solar ${r.solar_w}W"></div>
        <div class="bar" style="height:${(r.load_w/maxW*100).toFixed(1)}%;background:var(--blue)" title="Load ${r.load_w}W"></div>
      </div>
      <div class="bar-label">${h}</div>
    </div>
  `).join('');
}

// ── Geolocation ────────────────────────────────────────────────
function useGeolocation() {
  const st = document.getElementById('loc-status');
  if (!navigator.geolocation) { st.textContent = 'Geolocation not supported'; return; }
  st.textContent = '⏳ Detecting...';
  navigator.geolocation.getCurrentPosition(async pos => {
    const lat = pos.coords.latitude.toFixed(6);
    const lon = pos.coords.longitude.toFixed(6);
    document.getElementById('inp-lat').value = lat;
    document.getElementById('inp-lon').value = lon;

    // Compute UTC offset from browser
    const offsetMin = -(new Date().getTimezoneOffset());
    document.getElementById('inp-tz').value = (offsetMin / 60).toFixed(1);

    // Reverse geocode via free API
    try {
      const geo = await fetch(`https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json`);
      const gj  = await geo.json();
      const city    = gj.address?.city || gj.address?.town || gj.address?.village || '';
      const country = gj.address?.country || '';
      document.getElementById('inp-city').value    = city;
      document.getElementById('inp-country').value = country;
      st.textContent = `✅ Detected: ${city}, ${country}`;
    } catch {
      st.textContent = `✅ Coords set (lat ${lat}, lon ${lon})`;
    }
  }, err => {
    st.textContent = '❌ ' + err.message;
  });
}

async function saveLocation() {
  const st = document.getElementById('loc-status');
  try {
    const payload = {
      city:                document.getElementById('inp-city').value,
      country:             document.getElementById('inp-country').value,
      latitude:            parseFloat(document.getElementById('inp-lat').value) || 0,
      longitude:           parseFloat(document.getElementById('inp-lon').value) || 0,
      timezone_offset_hrs: parseFloat(document.getElementById('inp-tz').value) || 5.5,
      system_id:           'default',
    };
    const res = await postApi('/api/location', payload);
    st.textContent = `✅ Saved: ${res.city || 'location stored'}`;
    refresh();
  } catch(e) {
    st.textContent = '❌ ' + e.message;
  }
}

// ── Boot ──────────────────────────────────────────────────────
(async () => {
  // Load existing location from settings
  try {
    const s = await api('/api/settings');
    if (s.city)      document.getElementById('inp-city').value    = s.city;
    if (s.country)   document.getElementById('inp-country').value = s.country;
    if (s.latitude)  document.getElementById('inp-lat').value     = s.latitude;
    if (s.longitude) document.getElementById('inp-lon').value     = s.longitude;
    if (s.timezone_offset_hrs) document.getElementById('inp-tz').value = s.timezone_offset_hrs;
  } catch {}
  refresh();
})();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def serve_ui():
    return DASHBOARD_HTML


# ──────────────────────────────────────────────────────────────
# 8.  ENTRY POINT
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "="*60)
    print("  ☀️  Solar Energy Advisor  —  starting server")
    print("="*60)
    print("  Dashboard →  http://localhost:8000")
    print("  API Docs  →  http://localhost:8000/docs")
    print("="*60 + "\n")
    uvicorn.run("solar_advisor:app", host="0.0.0.0", port=8000, reload=True)