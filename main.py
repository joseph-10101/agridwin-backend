from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, List
import requests
from datetime import datetime, date

app = FastAPI(title="Digital Twin Agronomy API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class PlotInput(BaseModel):
    geometry: Any
    area_ha: float
    centroid: List[float]
    crop_type: str
    sowing_date: str
    soil_type: str = "limono_argileux"  # sableux, limoneux, argileux, etc.
    color: str = "#10b981"
    points_count: int = 4

# Propriétés hydriques des sols (RU en mm d'eau par mètre de sol)
SOIL_DB = {
    "sableux": {"ru_mm_m": 70, "label": "Sableux (RU faible : 70 mm/m)"},
    "sablo_limoneux": {"ru_mm_m": 100, "label": "Sablo-limoneux (RU moyenne : 100 mm/m)"},
    "limono_argileux": {"ru_mm_m": 140, "label": "Limono-argileux (RU élevée : 140 mm/m)"},
    "argileux": {"ru_mm_m": 180, "label": "Argileux lourd (RU très élevée : 180 mm/m)"},
}

CROP_DB = {
    "Tomate (Serre primeur)": {"kc_ini": 0.6, "kc_mid": 1.15, "kc_end": 0.8, "l_ini": 30, "l_dev": 40, "l_mid": 45, "l_late": 30, "root_depth": 0.6, "p": 0.4},
    "Tomate (Plein champ / Industrielle)": {"kc_ini": 0.6, "kc_mid": 1.15, "kc_end": 0.7, "l_ini": 30, "l_dev": 40, "l_mid": 40, "l_late": 25, "root_depth": 0.7, "p": 0.4},
    "Olivier (Huile d'olive)": {"kc_ini": 0.65, "kc_mid": 0.70, "kc_end": 0.65, "l_ini": 60, "l_dev": 90, "l_mid": 120, "l_late": 90, "root_depth": 1.2, "p": 0.65},
    "Blé tendre (Farine panifiable)": {"kc_ini": 0.3, "kc_mid": 1.15, "kc_end": 0.25, "l_ini": 20, "l_dev": 50, "l_mid": 60, "l_late": 30, "root_depth": 0.9, "p": 0.55},
    "Agrumes (Oranger)": {"kc_ini": 0.7, "kc_mid": 0.65, "kc_end": 0.7, "l_ini": 60, "l_dev": 90, "l_mid": 120, "l_late": 90, "root_depth": 1.0, "p": 0.5},
    "Luzerne pérenne": {"kc_ini": 0.4, "kc_mid": 1.05, "kc_end": 0.9, "l_ini": 10, "l_dev": 20, "l_mid": 20, "l_late": 10, "root_depth": 1.2, "p": 0.55},
}

def get_crop_kc(crop_name: str, days_since_sowing: int) -> float:
    crop = CROP_DB.get(crop_name, {"kc_ini": 0.5, "kc_mid": 1.0, "kc_end": 0.7, "l_ini": 25, "l_dev": 35, "l_mid": 45, "l_late": 25})
    t1 = crop["l_ini"]
    t2 = t1 + crop["l_dev"]
    t3 = t2 + crop["l_mid"]
    t4 = t3 + crop["l_late"]

    if days_since_sowing <= t1:
        return crop["kc_ini"]
    elif days_since_sowing <= t2:
        return crop["kc_ini"] + ((days_since_sowing - t1) / (t2 - t1)) * (crop["kc_mid"] - crop["kc_ini"])
    elif days_since_sowing <= t3:
        return crop["kc_mid"]
    elif days_since_sowing <= t4:
        return crop["kc_mid"] - ((days_since_sowing - t3) / (t4 - t3)) * (crop["kc_mid"] - crop["kc_end"])
    else:
        return crop["kc_end"]

@app.post("/api/simulate")
def simulate_plot(plot: PlotInput):
    lat, lon = plot.centroid[0], plot.centroid[1]

    weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=temperature_2m_max,temperature_2m_min,et0_fao_evapotranspiration,precipitation_sum&forecast_days=14&timezone=auto"
    weather_res = requests.get(weather_url).json()

    daily = weather_res.get("daily", {})
    dates = daily.get("time", [])[:14]
    et0_list = daily.get("et0_fao_evapotranspiration", [4.5] * 14)[:14]
    rain_list = daily.get("precipitation_sum", [0.0] * 14)[:14]
    temp_max = daily.get("temperature_2m_max", [28.0] * 14)[:14]

    try:
        sowing_dt = datetime.strptime(plot.sowing_date, "%Y-%m-%d").date()
        current_dt = date.today()
        base_days = max(1, (current_dt - sowing_dt).days)
    except Exception:
        base_days = 30

    crop_info = CROP_DB.get(plot.crop_type, {"root_depth": 0.8, "p": 0.5})
    soil_info = SOIL_DB.get(plot.soil_type, {"ru_mm_m": 140})

    # Calcul pédologique exact de la Réserve Utile : RU (mm) = RU_unitaire (mm/m) * Profondeur_racines (m)
    ru_totale_mm = round(soil_info["ru_mm_m"] * crop_info.get("root_depth", 0.8), 1)
    rfu_mm = round(ru_totale_mm * crop_info.get("p", 0.5), 1)
    rs_mm = round(ru_totale_mm - rfu_mm, 1)

    current_soil_stock = round(ru_totale_mm * 0.8, 1)
    simulation_results = []
    irrigation_events = []
    total_water_need_m3 = 0.0

    for i in range(len(dates)):
        current_age = base_days + i
        kc_day = round(get_crop_kc(plot.crop_type, current_age), 2)
        et0 = et0_list[i] if et0_list[i] is not None else 4.0
        rain = rain_list[i] if rain_list[i] is not None else 0.0

        if current_soil_stock >= rs_mm:
            ks = 1.0
        else:
            ks = max(0.1, round(current_soil_stock / rs_mm, 2))

        etr_mm = round(et0 * kc_day * ks, 2)
        effective_rain = round(rain * 0.8, 2)
        current_soil_stock = min(ru_totale_mm, current_soil_stock + effective_rain)

        irrigation_dose_mm = 0.0
        irrigation_m3 = 0.0
        trigger_irrigation = False

        if (current_soil_stock - etr_mm) <= rs_mm:
            trigger_irrigation = True
            irrigation_dose_mm = round(ru_totale_mm - (current_soil_stock - etr_mm), 1)
            irrigation_m3 = round(irrigation_dose_mm * 10 * plot.area_ha, 1)
            current_soil_stock = ru_totale_mm
            total_water_need_m3 += irrigation_m3
            irrigation_events.append({
                "date": dates[i],
                "dose_mm": irrigation_dose_mm,
                "volume_m3": irrigation_m3
            })
        else:
            current_soil_stock = max(0.0, round(current_soil_stock - etr_mm, 1))

        simulation_results.append({
            "date": dates[i],
            "day_age": current_age,
            "kc_value": kc_day,
            "ks_value": ks,
            "soil_stock_mm": current_soil_stock,
            "temp_max": temp_max[i],
            "et0_mm": et0,
            "rain_mm": rain,
            "etr_mm": etr_mm,
            "irrigation_dose_mm": irrigation_dose_mm,
            "irrigation_m3": irrigation_m3,
            "trigger": trigger_irrigation,
        })

    return {
        "status": "success",
        "plot_summary": {
            "crop": plot.crop_type,
            "soil_type": plot.soil_type,
            "area_ha": plot.area_ha,
            "centroid": plot.centroid,
            "days_after_sowing": base_days,
            "root_depth_m": crop_info.get("root_depth", 0.8),
            "ru_mm": ru_totale_mm,
            "rfu_mm": rfu_mm,
            "rs_mm": rs_mm,
            "total_water_m3": round(total_water_need_m3, 1),
            "irrigation_count": len(irrigation_events),
        },
        "forecast_days": simulation_results,
        "irrigation_events": irrigation_events,
    }
