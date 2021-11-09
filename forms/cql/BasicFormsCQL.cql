library BasicFormsCQL version '1.0'

using FHIR version '3.0.0'

include FHIRHelpers version '3.0.0' called FHIRHelpers

codesystem "LOINC": 'http://loinc.org'
codesystem "SNOMED": '2.16.840.1.113883.6.96'
codesystem "RxNorm": 'http://www.nlm.nih.gov/research/umls/rxnorm'
codesystem "CPT": 'http://www.ama-assn.org/go/cpt'
codesystem "ICD9": '2.16.840.1.113883.6.42'
codesystem "ICD10": '2.16.840.1.113883.6.3'

context Patient

define "vital_signs_concepts": Concept {
    Code '85353-1' from "LOINC",
    Code '9279-1' from "LOINC",
    Code '8867-4' from "LOINC",
    Code '2708-6' from "LOINC",
    Code '8310-5' from "LOINC",
    Code '8302-2' from "LOINC",
    Code '9843-4' from "LOINC",
    Code '29463-7' from "LOINC",
    Code '39156-5' from "LOINC",
    Code '85354-9' from "LOINC",
    Code '8480-6' from "LOINC",
    Code '8462-4' from "LOINC",
    Code '8478-0' from "LOINC"
}

define "cbc_panel_concepts": Concept {
    Code '58410-2' from "LOINC",
    Code '6690-2' from "LOINC",
    Code '789-8' from "LOINC",
    Code '718-7' from "LOINC",
    Code '4544-3' from "LOINC",
    Code '787-2' from "LOINC",
    Code '785-6' from "LOINC",
    Code '786-4' from "LOINC",
    Code '21000-5' from "LOINC",
    Code '788-0' from "LOINC",
    Code '777-3' from "LOINC",
    Code '32207-3' from "LOINC",
    Code '32623-1' from "LOINC"
}

define "vital_signs": [Observation: code in "vital_signs_concepts"]

define "cbc_panel_obs": [Observation: code in "cbc_panel_concepts"]
define "sample_single_resource_return": "cbc_panel_obs"[0]
define "sample_single_string_return": 'Filipino, White, American Shoshone'