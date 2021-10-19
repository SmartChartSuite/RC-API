library vital_signs version '1.0'

using FHIR version '4.0.0'

include FHIRHelpers version '4.0.0' called FHIRHelpers

codesystem "LOINC": 'http://loinc.org'
codesystem "SNOMED": '2.16.840.1.113883.6.96'
codesystem "RxNorm": 'http://www.nlm.nih.gov/research/umls/rxnorm'
codesystem "CPT": 'http://www.ama-assn.org/go/cpt'
codesystem "ICD9": '2.16.840.1.113883.6.42'
codesystem "ICD10": '2.16.840.1.113883.6.3'

context Patient

// Concept defintiions
define "respiratory_rate_concept": Concept {
    Code '9279-1' from "LOINC"
}
define "heart_rate_concept": Concept {
    Code '8867-4' from "LOINC"
}
define "o2_saturation_in_arterial_blood_concept": Concept {    
    Code '2708-6' from "LOINC"
}
define "body_temp_concept": Concept {
    Code '8310-5' from "LOINC"
}
define "body_height_concept": Concept {
    Code '8302-2' from "LOINC"
}
define "bmi_percentile_concept": Concept {
    Code '59576-9' from "LOINC"
}
define "body_weight_concept": Concept {
    Code '29463-7' from "LOINC"
}
define "bmi_concept": Concept {
    Code '39156-5' from "LOINC"
}
define "o2_saturation_concept": Concept {
    Code '59408-5' from "LOINC"
}
define "systolic_blood_pressure_concept": Concept {
    Code '8480-6' from "LOINC"
}
define "diastolic_blood_pressure_concept": Concept {
    Code '8462-4' from "LOINC"
}
define "mean_blood_pressure_concept": Concept {
    Code '8478-0' from "LOINC"
}
define "inhaled_o2_concentration_concept": Concept {
    Code '3150-0' from "LOINC"
}
define "inhaled_o2_flow_rate_concept": Concept {
    Code '3151-8' from "LOINC"
}

// Retrieve definitions

define "respiratory_rate": [Observation: code in "respiratory_rate_concept"]
define "heart_rate": [Observation: code in "heart_rate_concept"]
define "o2_saturation_in_arterial_blood": [Observation: code in "o2_saturation_in_arterial_blood_concept"]
define "body_temp": [Observation: code in "body_temp_concept"]
define "body_height": [Observation: code in "body_height_concept"]
define "bmi_percentile": [Observation: code in "bmi_percentile_concept"]
define "body_weight": [Observation: code in "body_weight_concept"]
define "bmi": [Observation: code in "bmi_concept"]
define "o2_saturation": [Observation: code in "o2_saturation_concept"]
define "systolic_blood_pressure": [Observation: code in "systolic_blood_pressure_concept"]
define "diastolic_blood_pressure": [Observation: code in "diastolic_blood_pressure_concept"]
define "mean_blood_pressure": [Observation: code in "mean_blood_pressure_concept"]
define "inhaled_o2_concentration": [Observation: code in "inhaled_o2_concentration_concept"]
define "inhaled_o2_flow_rate": [Observation: code in "inhaled_o2_flow_rate_concept"]