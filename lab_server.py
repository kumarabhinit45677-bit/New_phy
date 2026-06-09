"""HTTP server with static files and Python physics/research API endpoints."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from pathlib import Path

from materials_database import get_material
from periodic_table import get_element
from physics_engine import BeamParameters, PhysicsEngine, result_to_serializable
from research_modules import Layer, ResearchSuite, damage_indices, material_health_index


class LabRequestHandler(SimpleHTTPRequestHandler):
    root = Path(__file__).resolve().parent
    suite = ResearchSuite()

    def do_GET(self) -> None:
        if self.path == "/api/health":
            self.send_json({"status": "ok", "service": "ion-beam-lab", "backend": "python"})
            return
        return super().do_GET()

    def do_POST(self) -> None:
        if self.path == "/api/simulate":
            payload = self.read_json()
            result = PhysicsEngine().calculate(parameters_from_payload(payload))
            self.send_json(result_to_serializable(result))
            return
        if self.path == "/api/research":
            payload = self.read_json()
            parameters = parameters_from_payload(payload)
            result = PhysicsEngine().calculate(parameters)
            self.send_json(research_summary(self.suite, parameters, result, payload))
            return
        if self.path == "/api/srim/compare":
            payload = self.read_json()
            parameters = parameters_from_payload(payload)
            result = PhysicsEngine().calculate(parameters)
            rows = self.suite.parse_srim_table(str(payload.get("srim_text", "")))
            self.send_json(self.suite.compare_srim(result, rows))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Unknown API endpoint")

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def send_json(self, payload: object) -> None:
        body = json.dumps(payload, default=float).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def parameters_from_payload(payload: dict) -> BeamParameters:
    return BeamParameters(
        ion=get_element(str(payload.get("ion", "Ar"))),
        target=get_material(str(payload.get("material", "Iron"))),
        charge_state=int(payload.get("charge_state", 1)),
        energy_kev=float(payload.get("energy_kev", 500.0)),
        fluence_ions_cm2=float(payload.get("fluence_ions_cm2", 1.0e13)),
        irradiation_time_s=float(payload.get("irradiation_time_s", 60.0)),
        let_kev_nm=float(payload.get("let_kev_nm", 0.35)),
        beam_current_na=float(payload.get("beam_current_na", 100.0)),
        beam_angle_deg=float(payload.get("beam_angle_deg", 0.0)),
        beam_spread_deg=float(payload.get("beam_spread_deg", 4.0)),
        beam_intensity=float(payload.get("beam_intensity", 4.0)),
        simulation_speed=float(payload.get("simulation_speed", 1.0)),
        mode=str(payload.get("mode", "Research")),
    )


def research_summary(suite: ResearchSuite, parameters: BeamParameters, result, payload: dict) -> dict:
    layers_payload = payload.get("layers") or [
        {"material": "PTFE (Teflon)", "thickness_nm": 120.0},
        {"material": "SiO2", "thickness_nm": 80.0},
        {"material": "Silicon", "thickness_nm": 300.0},
        {"material": "Copper", "thickness_nm": 100.0},
    ]
    layers = [Layer(get_material(layer["material"]), float(layer.get("thickness_nm", 100.0)), float(layer.get("temperature_k", 300.0))) for layer in layers_payload[:20]]
    return {
        "bragg_peak": suite.bragg_peak(parameters),
        "multilayer": suite.multilayer_target(parameters, layers),
        "time_evolution": suite.time_evolution(result, parameters.target),
        "property_evolution": suite.property_evolution(result, parameters.target),
        "damage_index": damage_indices(result),
        "health_index": material_health_index(result, parameters.target),
        "correlation_matrix": suite.correlation_matrix(result, parameters.target),
        "annealing": suite.annealing_recovery(result, float(payload.get("annealing_temperature_k", 700.0)), float(payload.get("annealing_duration_s", 3600.0))),
        "radiation_hardness": suite.material_explorer(filters={"radiation_resistant": True}, sort_by="radiation_hardness")[:10],
        "ion_track": suite.ion_track(result, parameters.fluence_ions_cm2),
        "semiconductor_device": suite.semiconductor_device(result, parameters.target),
        "polymer_irradiation": suite.polymer_irradiation(result, parameters.target),
        "surface_engineering": suite.surface_engineering(result),
        "uncertainty": suite.uncertainty(result),
        "digital_twin": suite.digital_twin(result, parameters.target),
        "publication": suite.publication_report(parameters, result),
    }
