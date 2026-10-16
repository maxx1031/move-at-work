-- ExRx exercise graph (personal use). Namespace/database: exrx/exrx.
--
-- Nodes:  exercise, muscle, joint, equipment
-- Edges:  exercise ->works->     muscle     (role: target | synergists | stabilizers | ...)
--         exercise ->moves->     joint      (kind: dynamic | static, action: Flexion | ...)
--         exercise ->uses->      equipment
--         exercise ->variation_of-> exercise

DEFINE ANALYZER OVERWRITE en
    TOKENIZERS blank, class, punct
    FILTERS lowercase, ascii, snowball(english);

-- exercise ------------------------------------------------------------------
DEFINE TABLE OVERWRITE exercise SCHEMALESS;
DEFINE FIELD OVERWRITE name ON exercise TYPE string;
DEFINE FIELD OVERWRITE section ON exercise TYPE string;          -- WeightExercises | Stretches | Plyometrics | Aerobic
DEFINE FIELD OVERWRITE folder ON exercise TYPE option<string>;   -- e.g. LatissimusDorsi
DEFINE FIELD OVERWRITE utility ON exercise TYPE option<string>;
DEFINE FIELD OVERWRITE mechanics ON exercise TYPE option<string>;
DEFINE FIELD OVERWRITE force ON exercise TYPE option<string>;
DEFINE FIELD OVERWRITE equipment ON exercise TYPE array<string> DEFAULT [];
DEFINE FIELD OVERWRITE body_regions ON exercise TYPE array<string> DEFAULT [];
DEFINE FIELD OVERWRITE muscle_groups ON exercise TYPE array<string> DEFAULT [];
DEFINE FIELD OVERWRITE target_muscles ON exercise TYPE array<string> DEFAULT [];
DEFINE FIELD OVERWRITE search_text ON exercise TYPE string;      -- name + muscles + equipment + instructions
DEFINE FIELD OVERWRITE embedding ON exercise TYPE option<array<float>>;

DEFINE INDEX OVERWRITE exercise_section ON exercise FIELDS section;
DEFINE INDEX OVERWRITE exercise_equipment ON exercise FIELDS equipment;
DEFINE INDEX OVERWRITE exercise_targets ON exercise FIELDS target_muscles;
DEFINE INDEX OVERWRITE exercise_name_ft ON exercise FIELDS name FULLTEXT ANALYZER en BM25;
DEFINE INDEX OVERWRITE exercise_text_ft ON exercise FIELDS search_text FULLTEXT ANALYZER en BM25;
DEFINE INDEX OVERWRITE exercise_vec ON exercise FIELDS embedding
    HNSW DIMENSION 384 DIST COSINE TYPE F32 EFC 150 M 12;

-- muscle / joint / equipment ---------------------------------------------------
DEFINE TABLE OVERWRITE muscle SCHEMALESS;
DEFINE FIELD OVERWRITE name ON muscle TYPE string;
DEFINE FIELD OVERWRITE aliases ON muscle TYPE array<string> DEFAULT [];
DEFINE INDEX OVERWRITE muscle_name_ft ON muscle FIELDS name FULLTEXT ANALYZER en BM25;

DEFINE TABLE OVERWRITE joint SCHEMALESS;
DEFINE FIELD OVERWRITE name ON joint TYPE string;

DEFINE TABLE OVERWRITE equipment SCHEMALESS;
DEFINE FIELD OVERWRITE name ON equipment TYPE string;

-- edges ----------------------------------------------------------------------
DEFINE TABLE OVERWRITE works TYPE RELATION FROM exercise TO muscle SCHEMALESS;
DEFINE FIELD OVERWRITE role ON works TYPE string;
DEFINE FIELD OVERWRITE label ON works TYPE option<string>;      -- e.g. "Triceps, Long Head"
DEFINE INDEX OVERWRITE works_role ON works FIELDS role;

DEFINE TABLE OVERWRITE moves TYPE RELATION FROM exercise TO joint SCHEMALESS;
DEFINE FIELD OVERWRITE kind ON moves TYPE option<string>;
DEFINE FIELD OVERWRITE action ON moves TYPE string;

DEFINE TABLE OVERWRITE uses TYPE RELATION FROM exercise TO equipment SCHEMALESS;
DEFINE TABLE OVERWRITE variation_of TYPE RELATION FROM exercise TO exercise SCHEMALESS;

-- query helpers (used by the VLM tools) ---------------------------------------

-- Compact exercise card: everything a model needs, no embedding.
DEFINE FUNCTION OVERWRITE fn::card($ex: record<exercise>) {
    RETURN (SELECT
        record::id(id) AS id, name, section, utility, mechanics, force, equipment,
        body_regions, muscle_groups, instructions, comments, media, source_url,
        (SELECT VALUE { muscle: out.name, role: role, label: label } FROM ->works) AS muscles,
        (SELECT VALUE { joint: out.name, kind: kind, action: action } FROM ->moves) AS articulations,
        (SELECT VALUE record::id(id) FROM ->variation_of->exercise) AS variation_of,
        (SELECT VALUE record::id(id) FROM <-variation_of<-exercise) AS variations
    FROM ONLY $ex);
};

-- Exercises that work a muscle (matched by name), optionally restricted to a role.
DEFINE FUNCTION OVERWRITE fn::by_muscle($muscle: string, $role: option<string>) {
    LET $ms = (SELECT VALUE id FROM muscle WHERE name @@ $muscle OR aliases CONTAINS $muscle);
    RETURN SELECT VALUE { id: record::id(in), name: in.name, muscle: out.name, role: role }
        FROM works
        WHERE out INSIDE $ms AND ($role IS NONE OR role = $role);
};
