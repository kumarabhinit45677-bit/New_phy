"""Advanced research modules for the virtual ion irradiation laboratory."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

from database import DATA_DIR, ScientificDatabase
from materials_database import Material, get_material
from periodic_table import Element, get_element
from physics_engine import BeamParameters, PhysicsEngine, SimulationResult


TIMELINE_SECONDS = [0, 10, 30, 60, 600, 1800, 3600, 21600, 86400, 604800, 2592000]

PROPERTY_GROUPS = {
    "structural": ["defect_density_cm3", "dpa", "track_density_cm2"],
    "chemical": ["surface_energy_mj_m2", "radical_fraction"],
    "surface": ["surface_roughness_nm", "sputtering_loss_nm"],
    "morphological": ["track_density_cm2", "surface_roughness_nm"],
    "mechanical": ["hardness_relative", "integrity_percent"],
    "electrical": ["conductivity_s_m", "carrier_mobility_percent"],
    "optical": ["bandgap_ev", "absorption_shift_percent"],
    "thermal": ["temperature_k", "thermal_conductivity_relative"],
    "magnetic": ["magnetic_order_relative"],
    "dielectric": ["dielectric_constant", "dielectric_loss_relative"],
    "radiation_damage": ["vacancy_density_cm3", "interstitial_density_cm3", "dpa"],
    "semiconductor": ["bandgap_ev", "leakage_current_multiplier", "carrier_mobility_percent"],
    "polymer": ["crosslink_density", "chain_scission_density", "carbonization_relative"],
}

EQUATION_LIBRARY = [
    {"name": "Linear Energy Transfer", "equation": "LET = -dE/dx", "units": "keV/nm", "meaning": "Energy deposited per ion path length."},
    {"name": "Stopping Power", "equation": "S = Se + Sn", "units": "keV/nm", "meaning": "Electronic plus nuclear energy loss."},
    {"name": "Dose", "equation": "D = Edep * fluence / mass", "units": "Gy", "meaning": "Energy deposited per unit mass."},
    {"name": "Beam Current", "equation": "I = qN/t", "units": "A", "meaning": "Charge delivered per time."},
    {"name": "Range", "equation": "R proportional to E^1.5", "units": "nm", "meaning": "Approximate penetration depth."},
    {"name": "DPA", "equation": "DPA = vacancy density / atomic density", "units": "dimensionless", "meaning": "Displacements per target atom."},
    {"name": "Sputtering Yield", "equation": "Y = atoms ejected / incident ion", "units": "atoms/ion", "meaning": "Surface atom removal efficiency."},
    {"name": "Energy Loss", "equation": "Delta E = integral S(x) dx", "units": "keV", "meaning": "Integrated stopping along depth."},
]


FACILITY_PRESETS = [
    {"name": "IUAC Pelletron", "type": "Tandem accelerator", "energy_range_mev": [0.5, 200], "available_ions": ["H", "He", "C", "O", "Si", "Ar", "Fe", "Ni", "Au"], "current_limits_na": [0.01, 1000]},
    {"name": "Cyclotron", "type": "Cyclotron", "energy_range_mev": [5, 70], "available_ions": ["H", "D", "He", "C", "O"], "current_limits_na": [1, 100000]},
    {"name": "Synchrotron", "type": "Synchrotron", "energy_range_mev": [100, 100000], "available_ions": ["H", "C", "O", "Ar", "Kr", "Xe", "U"], "current_limits_na": [0.001, 100]},
    {"name": "Heavy Ion Facility", "type": "Heavy-ion beamline", "energy_range_mev": [1, 1000], "available_ions": ["Ar", "Kr", "Xe", "Au", "Pb", "U"], "current_limits_na": [0.001, 10000]},
    {"name": "Medical Beamline", "type": "Therapy and radiobiology", "energy_range_mev": [20, 430], "available_ions": ["H", "He", "C", "O"], "current_limits_na": [0.1, 1000]},
]


CRYSTAL_TEMPLATES = {
    "FCC": {"unit_cell": "cubic", "symmetry": "Fm-3m", "positions": [[0, 0, 0], [0, 0.5, 0.5], [0.5, 0, 0.5], [0.5, 0.5, 0]]},
    "BCC": {"unit_cell": "cubic", "symmetry": "Im-3m", "positions": [[0, 0, 0], [0.5, 0.5, 0.5]]},
    "HCP": {"unit_cell": "hexagonal", "symmetry": "P63/mmc", "positions": [[0, 0, 0], [2 / 3, 1 / 3, 0.5]]},
    "Diamond Cubic": {"unit_cell": "cubic", "symmetry": "Fd-3m", "positions": [[0, 0, 0], [0.25, 0.25, 0.25], [0.5, 0.5, 0], [0.75, 0.75, 0.25]]},
    "Zinc Blende": {"unit_cell": "cubic", "symmetry": "F-43m", "positions": [[0, 0, 0], [0.25, 0.25, 0.25]]},
    "Wurtzite": {"unit_cell": "hexagonal", "symmetry": "P63mc", "positions": [[0, 0, 0], [1 / 3, 2 / 3, 0.5]]},
    "Perovskite": {"unit_cell": "cubic", "symmetry": "Pm-3m", "positions": [[0, 0, 0], [0.5, 0.5, 0.5], [0.5, 0.5, 0], [0.5, 0, 0.5], [0, 0.5, 0.5]]},
    "Amorphous": {"unit_cell": "non-periodic", "symmetry": "none", "positions": []},
}


@dataclass(frozen=True)
class Layer:
    material: Material
    thickness_nm: float
    temperature_k: float = 300.0


class ResearchSuite:
    def __init__(self, database: Optional[ScientificDatabase] = None) -> None:
        self.database = database or ScientificDatabase()
        self.physics = PhysicsEngine()

    def material_explorer(
        self,
        query: str = "",
        filters: Optional[Dict[str, object]] = None,
        sort_by: str = "density",
        descending: bool = True,
    ) -> List[Dict[str, object]]:
        filters = filters or {}
        records = self.database.search_materials(query)
        class_filter = filters.get("material_class")
        if class_filter:
            records = [record for record in records if record["material_class"] == class_filter]
        for key in ["density", "bandgap", "dielectric_constant", "thermal_conductivity"]:
            if filters.get(f"min_{key}") is not None:
                records = [record for record in records if record.get(key) is not None and float(record[key]) >= float(filters[f"min_{key}"])]
            if filters.get(f"max_{key}") is not None:
                records = [record for record in records if record.get(key) is not None and float(record[key]) <= float(filters[f"max_{key}"])]
        if filters.get("radiation_resistant"):
            records = [record for record in records if "high" in str(record.get("radiation_tolerance", "")).lower()]
        sort_aliases = {
            "conductivity": "electrical_conductivity",
            "hardness": "displacement_energy",
            "radiation_hardness": "radiation_hardness_score",
        }
        sort_key = sort_aliases.get(sort_by, sort_by)
        for record in records:
            record.setdefault("radiation_hardness_score", radiation_hardness_index(record)["score"])
        return sorted(records, key=lambda record: float(record.get(sort_key) or 0.0), reverse=descending)

    def crystal_structure(self, material: Material | Dict[str, object]) -> Dict[str, object]:
        record = material.to_dict() if isinstance(material, Material) else dict(material)
        structure_text = str(record.get("crystal_structure", "")).lower()
        if "diamond" in structure_text or record.get("name") in {"Silicon", "Diamond", "Germanium"}:
            template_name = "Diamond Cubic"
        elif "zinc" in structure_text or record.get("name") in {"GaAs", "InP", "ZnSe", "CdTe"}:
            template_name = "Zinc Blende"
        elif "wurtzite" in structure_text or record.get("name") in {"GaN", "AlN", "ZnO"}:
            template_name = "Wurtzite"
        elif "bcc" in structure_text or record.get("name") in {"Iron", "Tungsten", "Chromium"}:
            template_name = "BCC"
        elif "hcp" in structure_text or record.get("name") in {"Titanium", "Cobalt", "Magnesium"}:
            template_name = "HCP"
        elif "amorphous" in structure_text or "glass" in str(record.get("subclass", "")).lower():
            template_name = "Amorphous"
        else:
            template_name = "FCC"
        template = dict(CRYSTAL_TEMPLATES[template_name])
        template.update(
            {
                "material": record.get("name"),
                "crystal_structure": template_name,
                "lattice_constant_nm": record.get("lattice_constant_nm") or record.get("lattice_constant") or estimate_lattice_constant(record),
                "states": {
                    "before": "ordered reference lattice",
                    "during": "local thermal spike, recoil displacement, transient electron cloud",
                    "after": "vacancies, interstitials, defect clusters, and residual lattice distortion",
                },
            }
        )
        return template

    def bragg_peak(self, parameters: BeamParameters, comparison_symbols: Sequence[str] = ("H", "He", "Ar", "Kr", "Xe", "Au")) -> Dict[str, object]:
        base = self.physics.calculate(parameters)
        profile = base.profile
        peak_index = int(np.argmax(profile.let_kev_nm))
        peak_depth = float(profile.depth_nm[peak_index])
        peak_intensity = float(profile.let_kev_nm[peak_index])
        half_max = peak_intensity * 0.5
        above_half = profile.depth_nm[profile.let_kev_nm >= half_max]
        width = float(above_half[-1] - above_half[0]) if len(above_half) else 0.0
        comparisons = []
        for symbol in comparison_symbols:
            try:
                result = self.physics.calculate(replace_ion(parameters, get_element(symbol)))
            except KeyError:
                continue
            comparisons.append(
                {
                    "ion": symbol,
                    "range_nm": result.penetration_depth_nm,
                    "peak_let_kev_nm": float(np.max(result.profile.let_kev_nm)),
                    "bragg_peak_depth_nm": float(result.profile.depth_nm[int(np.argmax(result.profile.let_kev_nm))]),
                }
            )
        return {"peak_depth_nm": peak_depth, "peak_width_nm": width, "peak_intensity_kev_nm": peak_intensity, "comparisons": comparisons}

    def multilayer_target(self, parameters: BeamParameters, layers: Sequence[Layer]) -> Dict[str, object]:
        remaining_energy = parameters.energy_kev
        layer_results = []
        for index, layer in enumerate(layers[:20], start=1):
            layer_parameters = replace_target_energy(parameters, layer.material, remaining_energy)
            result = self.physics.calculate(layer_parameters)
            traversed_fraction = min(layer.thickness_nm / max(result.penetration_depth_nm, 1.0), 1.0)
            energy_loss = result.energy_deposited_kev * traversed_fraction
            remaining_energy = max(remaining_energy - energy_loss, 0.0)
            layer_results.append(
                {
                    "layer": index,
                    "material": layer.material.name,
                    "thickness_nm": layer.thickness_nm,
                    "temperature_k": layer.temperature_k,
                    "energy_in_kev": layer_parameters.energy_kev,
                    "energy_loss_kev": energy_loss,
                    "energy_out_kev": remaining_energy,
                    "let_kev_nm": result.let_kev_nm,
                    "dpa": result.radiation_damage_dpa * traversed_fraction,
                    "temperature_rise_k": result.temperature_rise_k * traversed_fraction,
                }
            )
            if remaining_energy <= 0:
                break
        return {"layers": layer_results, "final_energy_kev": remaining_energy}

    def time_evolution(self, result: SimulationResult, material: Material) -> List[Dict[str, float]]:
        records = []
        for seconds in TIMELINE_SECONDS:
            log_t = np.log10(seconds + 1.0)
            damage_factor = 1.0 - np.exp(-result.radiation_damage_dpa * (1.0 + 0.15 * log_t))
            recovery = min(0.18 * log_t, 0.65)
            effective_damage = max(damage_factor * (1.0 - recovery), 0.0)
            conductivity = material.electrical_conductivity * max(0.02, 1.0 - 0.75 * effective_damage)
            bandgap = (material.bandgap or 0.0) + 0.12 * effective_damage
            hardness = 1.0 + 0.35 * effective_damage
            records.append(
                {
                    "time_s": seconds,
                    "defect_density_cm3": result.defect_density_cm3 * max(0.1, 1.0 - recovery),
                    "temperature_k": 300.0 + result.temperature_rise_k * np.exp(-seconds / 600.0),
                    "conductivity_s_m": conductivity,
                    "bandgap_ev": bandgap,
                    "relative_hardness": hardness,
                }
            )
        return records

    def property_evolution(self, result: SimulationResult, material: Material) -> Dict[str, object]:
        """Three-stage material state model for before/during/after irradiation."""
        track = self.ion_track(result, result.beam_flux_ions_cm2_s * 60.0)
        surface = self.surface_engineering(result)
        polymer = self.polymer_irradiation(result, material)
        semiconductor = self.semiconductor_device(result, material)
        after_record = self.time_evolution(result, material)[-1]
        damage = damage_indices(result)
        health = material_health_index(result, material)

        base = {
            "conductivity_s_m": material.electrical_conductivity,
            "bandgap_ev": material.bandgap or 0.0,
            "hardness_relative": 1.0,
            "defect_density_cm3": 0.0,
            "temperature_k": 300.0,
            "crosslink_density": 0.0,
            "chain_scission_density": 0.0,
            "track_density_cm2": 0.0,
            "vacancy_density_cm3": 0.0,
            "interstitial_density_cm3": 0.0,
            "dpa": 0.0,
            "surface_roughness_nm": 1.0,
            "sputtering_loss_nm": 0.0,
            "dielectric_constant": material.dielectric_constant or 1.0,
            "dielectric_loss_relative": 1.0,
            "thermal_conductivity_relative": 1.0,
            "carrier_mobility_percent": 100.0,
            "leakage_current_multiplier": 1.0,
            "absorption_shift_percent": 0.0,
            "magnetic_order_relative": 1.0,
            "radical_fraction": 0.0,
            "carbonization_relative": 0.0,
            "integrity_percent": 100.0,
            "surface_energy_mj_m2": 25.0,
        }
        during = dict(base)
        during.update(
            {
                "conductivity_s_m": material.electrical_conductivity * max(0.03, 1.0 - 0.45 * damage["damage_score"] / 100.0),
                "bandgap_ev": (material.bandgap or 0.0) + 0.08 * damage["damage_score"] / 100.0,
                "hardness_relative": 1.0 + 0.25 * min(damage["damage_score"] / 100.0, 1.0),
                "defect_density_cm3": result.defect_density_cm3,
                "temperature_k": 300.0 + result.temperature_rise_k,
                "crosslink_density": polymer["crosslink_density_relative"],
                "chain_scission_density": polymer["chain_scission_relative"],
                "track_density_cm2": track["track_density_cm2"],
                "vacancy_density_cm3": damage["vacancy_density_cm3"],
                "interstitial_density_cm3": damage["interstitial_density_cm3"],
                "dpa": result.radiation_damage_dpa,
                "surface_roughness_nm": surface["surface_roughness_nm"],
                "sputtering_loss_nm": damage["sputtering_loss_nm"],
                "dielectric_loss_relative": 1.0 + 1.8 * min(result.radiation_damage_dpa, 1.0),
                "thermal_conductivity_relative": max(0.15, 1.0 - 0.35 * min(result.radiation_damage_dpa, 1.0)),
                "carrier_mobility_percent": max(2.0, 100.0 - semiconductor["mobility_degradation_percent"]),
                "leakage_current_multiplier": semiconductor["leakage_current_multiplier"],
                "absorption_shift_percent": 6.0 * min(damage["damage_score"] / 100.0, 1.0),
                "magnetic_order_relative": max(0.2, 1.0 - 0.25 * min(result.radiation_damage_dpa, 1.0)),
                "radical_fraction": polymer["radical_fraction"],
                "carbonization_relative": polymer["carbonization_relative"],
                "integrity_percent": health["material_integrity_percent"],
                "surface_energy_mj_m2": surface["surface_energy_mj_m2"],
            }
        )
        after = dict(during)
        after.update(
            {
                "conductivity_s_m": after_record["conductivity_s_m"],
                "bandgap_ev": after_record["bandgap_ev"],
                "hardness_relative": after_record["relative_hardness"],
                "defect_density_cm3": after_record["defect_density_cm3"],
                "temperature_k": after_record["temperature_k"],
                "integrity_percent": max(0.0, health["material_integrity_percent"] - 0.12 * damage["damage_score"]),
            }
        )
        stages = {"before": base, "during": during, "after": after}
        return {
            "stages": stages,
            "groups": {group: {stage: {metric: stages[stage][metric] for metric in metrics} for stage in stages} for group, metrics in PROPERTY_GROUPS.items()},
            "timeline": self.property_timeline(result, material),
            "heatmap": property_change_heatmap(stages),
            "radar": property_radar(stages),
            "live_changes": live_property_changes(base, during),
        }

    def property_timeline(self, result: SimulationResult, material: Material) -> List[Dict[str, float]]:
        records = []
        polymer = self.polymer_irradiation(result, material)
        track = self.ion_track(result, result.beam_flux_ions_cm2_s * 60.0)
        for record in self.time_evolution(result, material):
            progress = np.log10(record["time_s"] + 1.0) / np.log10(TIMELINE_SECONDS[-1] + 1.0)
            records.append(
                {
                    "time_s": record["time_s"],
                    "conductivity_s_m": record["conductivity_s_m"],
                    "bandgap_ev": record["bandgap_ev"],
                    "hardness_relative": record["relative_hardness"],
                    "defect_density_cm3": record["defect_density_cm3"],
                    "temperature_k": record["temperature_k"],
                    "crosslink_density": polymer["crosslink_density_relative"] * progress,
                    "track_density_cm2": track["track_density_cm2"] * progress,
                }
            )
        return records

    def correlation_matrix(self, result: SimulationResult, material: Material) -> Dict[str, object]:
        timeline = self.property_timeline(result, material)
        variables = ["time_s", "conductivity_s_m", "bandgap_ev", "hardness_relative", "temperature_k", "defect_density_cm3", "track_density_cm2"]
        data = np.array([[float(row[key]) for key in variables] for row in timeline], dtype=float)
        data[:, 0] = np.log10(data[:, 0] + 1.0)
        matrix = np.corrcoef(data, rowvar=False)
        matrix = np.nan_to_num(matrix, nan=0.0, posinf=1.0, neginf=-1.0)
        return {"variables": variables, "matrix": matrix.tolist()}

    def annealing_recovery(self, result: SimulationResult, temperature_k: float, duration_s: float) -> Dict[str, float]:
        activation_k = 950.0
        recovery_fraction = float(np.clip(1.0 - np.exp(-duration_s / 3600.0 * np.exp((temperature_k - activation_k) / 240.0)), 0.0, 0.98))
        return {
            "recovery_fraction": recovery_fraction,
            "remaining_defect_density_cm3": result.defect_density_cm3 * (1.0 - recovery_fraction),
            "conductivity_recovery_fraction": min(recovery_fraction * 0.85, 0.95),
            "lattice_restoration_fraction": min(recovery_fraction * 0.90, 0.98),
            "carrier_lifetime_recovery_fraction": min(recovery_fraction * 0.75, 0.90),
        }

    def facility_presets(self) -> List[Dict[str, object]]:
        path = DATA_DIR / "facilities.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["facilities"]
        return FACILITY_PRESETS

    def defect_maps(self, result: SimulationResult) -> Dict[str, List[float]]:
        return {
            "depth_nm": result.profile.depth_nm.tolist(),
            "vacancy_density": result.profile.vacancy_density.tolist(),
            "interstitial_density": result.profile.interstitial_density.tolist(),
            "defect_density": result.profile.defect_density.tolist(),
            "thermal_spike_k": result.profile.thermal_spike_k.tolist(),
        }

    def ion_track(self, result: SimulationResult, fluence_ions_cm2: float) -> Dict[str, float]:
        track_radius_nm = max(0.4, 0.85 * np.sqrt(max(result.let_kev_nm, 1.0e-9)))
        track_area_cm2 = np.pi * (track_radius_nm * 1.0e-7) ** 2
        track_density = fluence_ions_cm2
        return {
            "track_radius_nm": float(track_radius_nm),
            "track_length_nm": result.penetration_depth_nm,
            "track_density_cm2": track_density,
            "track_overlap_fraction": float(1.0 - np.exp(-track_density * track_area_cm2)),
        }

    def semiconductor_device(self, result: SimulationResult, material: Material, device_type: str = "MOSFET") -> Dict[str, float]:
        dose_factor = np.log10(result.radiation_damage_dpa + 1.0e-12) + 12
        dose_factor = max(dose_factor, 0.0)
        bandgap = material.bandgap or 1.0
        return {
            "device_type": device_type,
            "threshold_voltage_shift_v": 0.035 * dose_factor * (1.0 + bandgap / 4.0),
            "leakage_current_multiplier": float(np.exp(min(result.radiation_damage_dpa * 12.0, 8.0))),
            "mobility_degradation_percent": float(np.clip(result.radiation_damage_dpa * 180.0, 0.0, 95.0)),
            "carrier_lifetime_degradation_percent": float(np.clip(result.radiation_damage_dpa * 240.0, 0.0, 98.0)),
        }

    def polymer_irradiation(self, result: SimulationResult, material: Material) -> Dict[str, float]:
        fluence_factor = np.log10(max(result.beam_flux_ions_cm2_s * 60.0, 1.0)) / 18.0
        scission_bias = 0.55 if "PTFE" in material.name or "PVC" in material.name else 0.35
        crosslink = float(np.clip(result.let_kev_nm * fluence_factor * (1.0 - scission_bias), 0.0, 1.0))
        scission = float(np.clip(result.let_kev_nm * fluence_factor * scission_bias, 0.0, 1.0))
        return {
            "crosslink_density_relative": crosslink,
            "chain_scission_relative": scission,
            "carbonization_relative": float(np.clip(result.thermal_spike_peak_k / 3000.0, 0.0, 1.0)),
            "radical_fraction": float(np.clip(result.secondary_electrons_per_ion / 1.0e5, 0.0, 1.0)),
            "molecular_weight_retention": float(np.clip(1.0 - scission * 0.65 + crosslink * 0.15, 0.05, 1.2)),
        }

    def surface_engineering(self, result: SimulationResult) -> Dict[str, float]:
        fluence_term = np.log10(max(result.beam_flux_ions_cm2_s * 60.0, 1.0)) / 16.0
        roughness = 1.0 + 35.0 * result.sputtering_yield_atoms_ion * fluence_term
        surface_energy = 25.0 + 18.0 * min(result.let_kev_nm, 5.0)
        return {
            "surface_roughness_nm": float(roughness),
            "surface_energy_mj_m2": float(surface_energy),
            "contact_angle_deg": float(np.clip(110.0 - surface_energy * 0.55, 10.0, 130.0)),
            "adhesion_relative": float(np.clip(surface_energy / 60.0, 0.1, 2.0)),
        }

    def recommend(self, goal: str, candidate_symbols: Sequence[str], candidate_materials: Sequence[str]) -> Dict[str, object]:
        goal_l = goal.lower()
        ion_scores = []
        for symbol in candidate_symbols:
            element = get_element(symbol)
            if "conduct" in goal_l:
                score = 100.0 / max(element.atomic_number, 1)
                energy = 150.0
                fluence = 5.0e14
            elif "hard" in goal_l:
                score = element.atomic_number * 0.8
                energy = 900.0
                fluence = 1.0e15
            else:
                score = 50.0 + element.atomic_number * 0.15
                energy = 500.0
                fluence = 1.0e14
            ion_scores.append({"ion": symbol, "score": score, "energy_kev": energy, "fluence_ions_cm2": fluence})
        material_scores = [
            {"material": name, "radiation_hardness": radiation_hardness_index(get_material(name).to_dict())["score"]}
            for name in candidate_materials
            if name
        ]
        return {
            "goal": goal,
            "recommended_ion": max(ion_scores, key=lambda item: item["score"]),
            "recommended_material": max(material_scores, key=lambda item: item["radiation_hardness"]) if material_scores else None,
            "ranked_ions": sorted(ion_scores, key=lambda item: item["score"], reverse=True),
        }

    def reverse_engineer(self, desired_outputs: Dict[str, float]) -> Dict[str, object]:
        target_bandgap = desired_outputs.get("bandgap", 0.0)
        target_hardness = desired_outputs.get("hardness_percent", 0.0)
        if target_bandgap >= 3.0:
            material = "GaN" if target_bandgap < 4.5 else "Diamond"
            ion = "He"
            energy = 350.0
        elif target_hardness > 20:
            material = "PEEK" if "PEEK" in desired_outputs.get("material_hint", "PEEK") else "Iron"
            ion = "Ar"
            energy = 850.0
        else:
            material = "Silicon"
            ion = "H"
            energy = 120.0
        return {"best_ion": ion, "best_material": material, "energy_kev": energy, "fluence_ions_cm2": 7.5e14, "irradiation_time_s": 600.0}

    def uncertainty(self, result: SimulationResult, energy_pct: float = 2.0, fluence_pct: float = 5.0, current_pct: float = 1.0) -> Dict[str, Dict[str, float]]:
        combined = np.sqrt((energy_pct / 100.0) ** 2 + (fluence_pct / 100.0) ** 2 + (current_pct / 100.0) ** 2)
        outputs = {
            "range_nm": result.penetration_depth_nm,
            "let_kev_nm": result.let_kev_nm,
            "dpa": result.radiation_damage_dpa,
            "temperature_rise_k": result.temperature_rise_k,
        }
        return {
            key: {"value": value, "low": value * (1.0 - combined), "high": value * (1.0 + combined), "relative_uncertainty": combined}
            for key, value in outputs.items()
        }

    def grand_comparison(self, parameters: BeamParameters, ion_symbols: Sequence[str], material_names: Sequence[str]) -> List[Dict[str, object]]:
        rows = []
        for symbol in ion_symbols[:10]:
            for material_name in material_names[:10]:
                result = self.physics.calculate(replace_target_ion(parameters, get_material(material_name), get_element(symbol)))
                rows.append(
                    {
                        "ion": symbol,
                        "material": material_name,
                        "range_nm": result.penetration_depth_nm,
                        "let_kev_nm": result.let_kev_nm,
                        "dpa": result.radiation_damage_dpa,
                        "radiation_hardness_score": radiation_hardness_index(get_material(material_name).to_dict())["score"],
                        "damage_score": material_damage_score(result),
                    }
                )
        return sorted(rows, key=lambda row: row["damage_score"])

    def digital_twin(self, result: SimulationResult, material: Material) -> Dict[str, object]:
        annealed = self.annealing_recovery(result, 700.0, 3600.0)
        return {
            "original_material": {"conductivity": material.electrical_conductivity, "bandgap": material.bandgap, "defect_density": 0.0},
            "current_irradiation_state": {"conductivity": material.electrical_conductivity * 0.75, "bandgap": (material.bandgap or 0.0) + 0.04, "defect_density": result.defect_density_cm3},
            "predicted_final_state": self.time_evolution(result, material)[-1],
            "recovered_state": annealed,
        }

    def notebook_entry(self, parameters: BeamParameters, result: SimulationResult, notes: str = "") -> Dict[str, object]:
        return {
            "date": datetime.now().isoformat(timespec="seconds"),
            "ion": parameters.ion.symbol,
            "material": parameters.target.name,
            "energy_kev": parameters.energy_kev,
            "fluence_ions_cm2": parameters.fluence_ions_cm2,
            "let_kev_nm": result.let_kev_nm,
            "beam_current_na": result.beam_current_na,
            "results": result.scalar_outputs(),
            "notes": notes,
        }

    def publication_report(self, parameters: BeamParameters, result: SimulationResult) -> Dict[str, str]:
        material = parameters.target.name
        ion = parameters.ion.symbol
        return {
            "abstract": f"{ion} ion irradiation of {material} was simulated using a coupled stopping, damage, and thermal-spike model.",
            "methodology": f"Incident {ion}{parameters.charge_state}+ ions at {parameters.energy_kev:.1f} keV were propagated through {material}. Electronic and nuclear stopping were integrated over depth.",
            "results": f"The estimated range is {result.penetration_depth_nm:.2f} nm with LET {result.let_kev_nm:.4f} keV/nm and DPA {result.radiation_damage_dpa:.3e}.",
            "discussion": "Nuclear energy deposition controls vacancy production, while electronic energy deposition controls secondary electron generation and thermal excitation.",
            "conclusion": "The selected irradiation condition produces quantifiable track damage and can be compared with alternate ions, materials, and annealing schedules.",
            "figure_caption": f"Depth-resolved energy, LET, stopping, and defect profiles for {ion} in {material}.",
        }

    def parse_srim_table(self, text: str) -> List[Dict[str, float]]:
        rows: List[Dict[str, float]] = []
        for raw in text.splitlines():
            clean = raw.strip().replace(",", " ")
            if not clean or clean.startswith("#"):
                continue
            parts = clean.split()
            numeric = []
            for part in parts:
                try:
                    numeric.append(float(part))
                except ValueError:
                    pass
            if len(numeric) >= 4:
                rows.append({"energy_kev": numeric[0], "electronic_stopping": numeric[1], "nuclear_stopping": numeric[2], "range_nm": numeric[3]})
        return rows

    def compare_srim(self, result: SimulationResult, srim_rows: Sequence[Dict[str, float]]) -> Dict[str, object]:
        if not srim_rows:
            return {"rows": [], "range_error_percent": None}
        nearest = min(srim_rows, key=lambda row: abs(row["range_nm"] - result.penetration_depth_nm))
        return {
            "nearest_srim": nearest,
            "simulator_range_nm": result.penetration_depth_nm,
            "range_error_percent": 100.0 * (result.penetration_depth_nm - nearest["range_nm"]) / max(nearest["range_nm"], 1.0e-9),
            "rows": list(srim_rows),
        }


def replace_target_energy(parameters: BeamParameters, target: Material, energy_kev: float) -> BeamParameters:
    return BeamParameters(
        ion=parameters.ion,
        target=target,
        charge_state=parameters.charge_state,
        energy_kev=max(energy_kev, 0.1),
        fluence_ions_cm2=parameters.fluence_ions_cm2,
        irradiation_time_s=parameters.irradiation_time_s,
        let_kev_nm=parameters.let_kev_nm,
        beam_current_na=parameters.beam_current_na,
        beam_angle_deg=parameters.beam_angle_deg,
        beam_spread_deg=parameters.beam_spread_deg,
        beam_intensity=parameters.beam_intensity,
        simulation_speed=parameters.simulation_speed,
        mode=parameters.mode,
    )


def replace_ion(parameters: BeamParameters, ion: Element) -> BeamParameters:
    return replace_target_ion(parameters, parameters.target, ion)


def replace_target_ion(parameters: BeamParameters, target: Material, ion: Element) -> BeamParameters:
    return BeamParameters(
        ion=ion,
        target=target,
        charge_state=parameters.charge_state,
        energy_kev=parameters.energy_kev,
        fluence_ions_cm2=parameters.fluence_ions_cm2,
        irradiation_time_s=parameters.irradiation_time_s,
        let_kev_nm=parameters.let_kev_nm,
        beam_current_na=parameters.beam_current_na,
        beam_angle_deg=parameters.beam_angle_deg,
        beam_spread_deg=parameters.beam_spread_deg,
        beam_intensity=parameters.beam_intensity,
        simulation_speed=parameters.simulation_speed,
        mode=parameters.mode,
    )


def estimate_lattice_constant(record: Dict[str, object]) -> float:
    density = float(record.get("density") or 1.0)
    mass = float(record.get("atomic_mass") or 50.0)
    return float(np.clip(0.18 * (mass / density) ** (1.0 / 3.0), 0.18, 1.2))


def radiation_hardness_index(record: Dict[str, object]) -> Dict[str, object]:
    density = float(record.get("density") or 1.0)
    displacement = float(record.get("displacement_energy") or 25.0)
    thermal = float(record.get("thermal_conductivity") or 1.0)
    bandgap = float(record.get("bandgap") or 0.0)
    tolerance = str(record.get("radiation_tolerance", "")).lower()
    score = 18.0 * np.log10(thermal + 1.0) + 0.45 * displacement + 4.0 * bandgap + 2.0 * density
    if "very high" in tolerance:
        score += 25
    elif "high" in tolerance:
        score += 15
    elif "low" in tolerance:
        score -= 12
    score = float(np.clip(score, 0.0, 100.0))
    if score >= 80:
        category = "Excellent"
    elif score >= 60:
        category = "Good"
    elif score >= 35:
        category = "Moderate"
    else:
        category = "Poor"
    return {"score": score, "category": category}


def damage_indices(result: SimulationResult) -> Dict[str, object]:
    affected_thickness_cm = max(result.penetration_depth_nm, 1.0) * 1.0e-7
    vacancy_density = result.vacancies_per_ion * result.beam_flux_ions_cm2_s * 60.0 / affected_thickness_cm
    interstitial_density = result.interstitials_per_ion * result.beam_flux_ions_cm2_s * 60.0 / affected_thickness_cm
    frenkel_density = min(vacancy_density, interstitial_density)
    sputtering_loss_nm = result.sputtering_yield_atoms_ion * result.beam_flux_ions_cm2_s * 60.0 * 1.0e-16
    score = float(
        np.clip(
            16.0 * np.log10(result.radiation_damage_dpa + 1.0)
            + 10.0 * np.log10(result.defect_density_cm3 / 1.0e16 + 1.0)
            + 7.5 * result.sputtering_yield_atoms_ion
            + result.temperature_rise_k / 700.0,
            0.0,
            100.0,
        )
    )
    if score >= 80:
        classification = "Critical"
    elif score >= 55:
        classification = "Heavy"
    elif score >= 25:
        classification = "Moderate"
    else:
        classification = "Minimal"
    return {
        "vacancy_density_cm3": float(vacancy_density),
        "interstitial_density_cm3": float(interstitial_density),
        "frenkel_pair_density_cm3": float(frenkel_density),
        "defect_density_cm3": result.defect_density_cm3,
        "dpa": result.radiation_damage_dpa,
        "sputtering_loss_nm": float(sputtering_loss_nm),
        "damage_score": score,
        "classification": classification,
    }


def material_health_index(result: SimulationResult, material: Material) -> Dict[str, object]:
    damage = damage_indices(result)
    hardness = radiation_hardness_index(material.to_dict())["score"]
    damage_percent = damage["damage_score"]
    resistance = float(np.clip(0.72 * hardness + 0.28 * (100.0 - damage_percent), 0.0, 100.0))
    integrity = float(np.clip(100.0 - 0.78 * damage_percent + 0.12 * hardness, 0.0, 100.0))
    lifetime = float(np.clip(100.0 - damage_percent * (1.25 - hardness / 160.0), 0.0, 100.0))
    if damage_percent >= 75:
        state = "Severe Damage"
        color = "red"
    elif damage_percent >= 50:
        state = "Damaged"
        color = "orange"
    elif damage_percent >= 25:
        state = "Moderate"
        color = "yellow"
    else:
        state = "Safe"
        color = "green"
    return {
        "material_integrity_percent": integrity,
        "material_damage_percent": damage_percent,
        "radiation_resistance_percent": resistance,
        "remaining_lifetime_percent": lifetime,
        "status": state,
        "color": color,
    }


def percent_change(before: float, after: float) -> float:
    denominator = abs(before) if abs(before) > 1.0e-30 else 1.0
    return float(100.0 * (after - before) / denominator)


def property_status(change_percent: float) -> str:
    if change_percent >= 10.0:
        return "Improved"
    if change_percent <= -35.0:
        return "Severely Damaged"
    if change_percent <= -5.0:
        return "Degraded"
    return "Stable"


def live_property_changes(before: Dict[str, float], during: Dict[str, float]) -> Dict[str, Dict[str, object]]:
    keys = ["conductivity_s_m", "bandgap_ev", "hardness_relative", "surface_roughness_nm", "defect_density_cm3", "temperature_k"]
    changes = {}
    for key in keys:
        change = percent_change(float(before.get(key, 0.0)), float(during.get(key, 0.0)))
        changes[key] = {"change_percent": change, "status": property_status(change)}
    return changes


def property_change_heatmap(stages: Dict[str, Dict[str, float]]) -> List[Dict[str, object]]:
    rows = []
    for key in ["conductivity_s_m", "bandgap_ev", "hardness_relative", "defect_density_cm3", "temperature_k", "crosslink_density", "track_density_cm2", "dpa"]:
        values = {stage: float(data.get(key, 0.0)) for stage, data in stages.items()}
        before = values["before"]
        max_change = max(abs(percent_change(before, values["during"])), abs(percent_change(before, values["after"])))
        if max_change >= 75:
            intensity = "Extreme"
        elif max_change >= 35:
            intensity = "High"
        elif max_change >= 10:
            intensity = "Moderate"
        else:
            intensity = "Low"
        rows.append({"property": key, "values": values, "max_change_percent": max_change, "intensity": intensity})
    return rows


def property_radar(stages: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    def score(stage: Dict[str, float]) -> Dict[str, float]:
        damage_scale = np.clip(float(stage.get("dpa", 0.0)), 0.0, 1.0)
        defect_scale = np.clip(np.log10(float(stage.get("defect_density_cm3", 0.0)) / 1.0e12 + 1.0) / 8.0, 0.0, 1.0)
        return {
            "electrical": float(np.clip(np.log10(abs(stage.get("conductivity_s_m", 0.0)) + 1.0) / 8.0 * 100.0, 0.0, 100.0)),
            "mechanical": float(np.clip(stage.get("hardness_relative", 1.0) * 55.0, 0.0, 100.0)),
            "thermal": float(np.clip(100.0 - (stage.get("temperature_k", 300.0) - 300.0) / 18.0, 0.0, 100.0)),
            "optical": float(np.clip(100.0 - abs(stage.get("absorption_shift_percent", 0.0)) * 8.0, 0.0, 100.0)),
            "chemical": float(np.clip(100.0 - stage.get("radical_fraction", 0.0) * 70.0, 0.0, 100.0)),
            "structural": float(np.clip(100.0 - defect_scale * 100.0, 0.0, 100.0)),
            "surface": float(np.clip(100.0 - stage.get("surface_roughness_nm", 1.0) * 2.0, 0.0, 100.0)),
            "radiation_resistance": float(np.clip(100.0 - damage_scale * 100.0, 0.0, 100.0)),
        }

    return {stage: score(values) for stage, values in stages.items()}


def material_damage_score(result: SimulationResult) -> float:
    return float(np.clip(40.0 * result.radiation_damage_dpa + 10.0 * result.sputtering_yield_atoms_ion + result.temperature_rise_k / 1000.0, 0.0, 100.0))

