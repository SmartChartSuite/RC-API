
        library temporary_retrieve version '1.0'

        using FHIR version '3.0.0'

        include FHIRHelpers version '3.0.0' called FHIRHelpers

        codesystem "LOINC": 'http://loinc.org'
        codesystem "SNOMED": '2.16.840.1.113883.6.96'
        codesystem "RxNorm": 'http://www.nlm.nih.gov/research/umls/rxnorm'
        codesystem "CPT": 'http://www.ama-assn.org/go/cpt'
        codesystem "ICD9": '2.16.840.1.113883.6.42'
        codesystem "ICD10": '2.16.840.1.113883.6.3'
        codesystem USCoreEthnicitySystem: '2.16.840.1.113883.6.238'
        codesystem RelationshipType: '2.16.840.1.113883.4.642.3.449'


        

        context Patient




        define "mv_ppcon_oth_concepts": Concept {
            Code '27624003' from "SNOMED", 
            Code '373621006' from "SNOMED", 
            Code '203082005' from "SNOMED", 
            Code '32022003' from "SNOMED", 
            Code '398155003' from "SNOMED", 
            Code '74025007' from "SNOMED", 
            Code '65460003' from "SNOMED", 
            Code '710166008' from "SNOMED", 
            Code '431237007' from "SNOMED", 
            Code '26649005' from "SNOMED", 
            Code '35545005' from "SNOMED", 
            Code '191268006' from "SNOMED", 
            Code '128290005' from "SNOMED", 
            Code '9418005' from "SNOMED", 
            Code '78609007' from "SNOMED", 
            Code '25913001' from "SNOMED", 
            Code '92811003' from "SNOMED", 
            Code '724748004' from "SNOMED", 
            Code '58184002' from "SNOMED", 
            Code '28590005' from "SNOMED", 
            Code '49774006' from "SNOMED", 
            Code '122981000119101' from "SNOMED", 
            Code '17097001' from "SNOMED", 
            Code '63988001' from "SNOMED", 
            Code '128293007' from "SNOMED", 
            Code '177010002' from "SNOMED", 
            Code '128297008' from "SNOMED", 
            Code '110281001' from "SNOMED", 
            Code '128237006' from "SNOMED", 
            Code '267613004' from "SNOMED", 
            Code '714252004' from "SNOMED", 
            Code '51160004' from "SNOMED", 
            Code '128289001' from "SNOMED", 
            Code '128294001' from "SNOMED", 
            Code '128292002' from "SNOMED", 
            Code '95223005' from "SNOMED", 
            Code '736464002' from "SNOMED", 
            Code '722955006' from "SNOMED", 
            Code '128300003' from "SNOMED", 
            Code '128236002' from "SNOMED", 
            Code '128283000' from "SNOMED", 
            Code '762594002' from "SNOMED", 
            Code '128529002' from "SNOMED", 
            Code '128284006' from "SNOMED", 
            Code '431953002' from "SNOMED", 
            Code '11892641000119100' from "SNOMED", 
            Code '413834006' from "SNOMED", 
            Code '314017009' from "SNOMED", 
            Code '773328002' from "SNOMED", 
            Code '787094005' from "SNOMED", 
            Code '12281000132104' from "SNOMED", 
            Code '830057003' from "SNOMED", 
            Code '128301004' from "SNOMED", 
            Code '14931000119109' from "SNOMED", 
            Code '45941008' from "SNOMED", 
            Code '128296004' from "SNOMED", 
            Code '18780001000004100' from "SNOMED", 
            Code '413835007' from "SNOMED", 
            Code '762596000' from "SNOMED", 
            Code '15734361000119100' from "SNOMED", 
            Code '15733681000119100' from "SNOMED", 
            Code '15734521000119100' from "SNOMED", 
            Code '15733601000119100' from "SNOMED", 
            Code '770667002' from "SNOMED", 
            Code '780817000' from "SNOMED"
        }




       define "mv_ppcon_oth":
            	 [Condition: Code in "mv_ppcon_oth_concepts"]


