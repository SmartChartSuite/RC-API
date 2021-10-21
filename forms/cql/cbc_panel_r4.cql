library cbc_panel version '1.0'

using FHIR version '4.0.0'

include FHIRHelpers version '4.0.0' called FHIRHelpers

codesystem "LOINC": 'http://loinc.org'
codesystem "SNOMED": '2.16.840.1.113883.6.96'
codesystem "RxNorm": 'http://www.nlm.nih.gov/research/umls/rxnorm'
codesystem "CPT": 'http://www.ama-assn.org/go/cpt'
codesystem "ICD9": '2.16.840.1.113883.6.42'
codesystem "ICD10": '2.16.840.1.113883.6.3'

context Patient

// Concept Defintiions
define "wbc_vol_concept": Concept {
    Code '6690-2' from "LOINC"
}
define "rbc_vol_concept": Concept {
    Code '789-8' from "LOINC"
}
define "hemoglobin_mass_vol_concept": Concept {
    Code '718-7' from "LOINC"
}
define "hematocrit_vol_frac_concept": Concept {
    Code '4544-3' from "LOINC"
}
define "mcv_ent_vol_concept": Concept {
    Code '787-2' from "LOINC"
}
define "mch_ent_mass_concept": Concept {
    Code '785-6' from "LOINC"
}
define "mchc_mass_vol_concept": Concept {
    Code '786-4' from "LOINC"
}
define "rbc_ent_vol_concept": Concept {
    Code '21000-5' from "LOINC"
}
define "rbc_ratio_concept": Concept {
    Code '788-0' from "LOINC"
}
define "platelets_vol_concept": Concept {
    Code '777-3' from "LOINC"
}
define "platelets_dw_ent_vol_concept": Concept {
    Code '32207-3' from "LOINC"
}
define "platelets_mv_ent_vol_concept": Concept {
    Code '32623-1' from "LOINC"
}

// Retrieval Definitions
define "wbc_vol": [Observation: code in "wbc_vol_concept"]
define "rbc_vol": [Observation: code in "rbc_vol_concept"]
define "rbc_snapshot": "rbc_vol"[0]
define "hemoglobin_mass_vol": [Observation: code in "hemoglobin_mass_vol_concept"]
define "hematocrit_vol_frac": [Observation: code in "hematocrit_vol_frac_concept"]
define "mcv_ent_vol": [Observation: code in "mcv_ent_vol_concept"]
define "mch_ent_mass": [Observation: code in "mch_ent_mass_concept"]
define "mchc_mass_vol": [Observation: code in "mchc_mass_vol_concept"]
define "rbc_ent_vol": [Observation: code in "rbc_ent_vol_concept"]
define "rbc_ratio": [Observation: code in "rbc_ratio_concept"]
define "platelets_vol": [Observation: code in "platelets_vol_concept"]
define "platelets_dw_ent_vol": [Observation: code in "platelets_dw_ent_vol_concept"]
define "platelets_mv_ent_vol": [Observation: code in "platelets_mv_ent_vol_concept"]