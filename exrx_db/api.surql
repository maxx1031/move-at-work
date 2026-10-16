-- Server-side query layer + HTTP endpoints for the ExRx graph (applied by load.py after schema.surql).
--
-- Endpoints live at /api/exrx/exrx/<path>; every one requires header `x-api-key: <EXRX_API_KEY>`
-- (checked by fn::require_key; PERMISSIONS clauses cannot see $request, so the gate is middleware).
--   GET  /search?q=&section=&equipment=&muscle=&role=&limit=   full-text search
--   POST /search  {q, vector?, section?, equipment?, muscle?, role?, limit?}  hybrid when vector given
--   GET  /exercise/:id            full card
--   GET  /muscle/:name?role=&limit=
--   GET  /joint/:joint?action=&kind=&limit=
--   GET  /similar/:id?limit=
--   GET  /values/:field           sections | equipment | muscles | joints | roles | actions

-- shared filters -------------------------------------------------------------
DEFINE FUNCTION OVERWRITE fn::matches($ex: record<exercise>, $section: option<string>,
    $equipment: option<string>, $muscle: option<string>, $role: option<string>) -> bool {
    RETURN ($section IS NONE OR $ex.section = $section)
        AND ($equipment IS NONE OR string::contains(
            string::lowercase(array::join($ex.equipment, '|')), string::lowercase($equipment)))
        AND ($muscle IS NONE OR string::contains(
            string::lowercase(array::join($ex->works[WHERE $role IS NONE OR role = $role].label, '|')),
            string::lowercase($muscle)));
};

DEFINE FUNCTION OVERWRITE fn::row($ex: record<exercise>) -> object {
    RETURN { id: record::id($ex), name: $ex.name, section: $ex.section,
             equipment: $ex.equipment, target_muscles: $ex.target_muscles };
};

-- hybrid search: BM25 on names (all terms must match) + vector, weighted reciprocal-rank fusion (k = 60).
-- Exact-name queries ("goblet squat") get a name boost; descriptive sentences match no name and
-- fall back to the vector ranking, which handles descriptions far better than keywords.
DEFINE FUNCTION OVERWRITE fn::search($q: string, $vec: option<array<float>>,
    $section: option<string>, $equipment: option<string>, $muscle: option<string>,
    $role: option<string>, $limit: option<int>) -> array {
    LET $n = $limit ?? 10;
    LET $filtered = $section IS NOT NONE OR $equipment IS NOT NONE OR $muscle IS NOT NONE;
    LET $text = (SELECT VALUE id FROM (
        SELECT id, search::score(1) AS s FROM exercise WHERE name @1@ $q ORDER BY s DESC LIMIT 200
    ) WHERE fn::matches(id, $section, $equipment, $muscle, $role) LIMIT 50);
    LET $vecs = IF $vec IS NONE { [] }
        ELSE IF $filtered {
            (SELECT VALUE id FROM (
                SELECT id, vector::similarity::cosine(embedding, $vec) AS s FROM exercise
                WHERE embedding IS NOT NONE AND fn::matches(id, $section, $equipment, $muscle, $role)
                ORDER BY s DESC LIMIT 50))
        } ELSE {
            (SELECT VALUE id FROM (
                SELECT id, vector::distance::knn() AS d FROM exercise
                WHERE embedding <|50,80|> $vec ORDER BY d))
        };
    LET $scored = array::union($text, $vecs).map(|$id| {
        id: $id,
        score: (IF array::find_index($text, $id) IS NONE { 0.0 } ELSE { 0.5 / (61 + array::find_index($text, $id)) })
             + (IF array::find_index($vecs, $id) IS NONE { 0.0 } ELSE { 1.0 / (61 + array::find_index($vecs, $id)) })
    });
    RETURN (SELECT * FROM $scored ORDER BY score DESC LIMIT $n)
        .map(|$r| fn::row($r.id) + { score: math::fixed($r.score, 4) });
};

DEFINE FUNCTION OVERWRITE fn::muscle_exercises($muscle: string, $role: option<string>,
    $limit: option<int>) -> array {
    LET $m = string::lowercase($muscle);
    RETURN (SELECT VALUE fn::row(id) + {
            roles: ->works[WHERE string::contains(string::lowercase(label), $m)
                AND ($role IS NONE OR role = $role)].role }
        FROM exercise
        WHERE string::contains(string::lowercase(array::join(
            ->works[WHERE $role IS NONE OR role = $role].label, '|')), $m)
        LIMIT $limit ?? 50);
};

DEFINE FUNCTION OVERWRITE fn::joint_exercises($joint: string, $action: option<string>,
    $kind: option<string>, $limit: option<int>) -> array {
    LET $j = string::lowercase($joint);
    LET $a = IF $action IS NONE { NONE } ELSE { string::lowercase($action) };
    RETURN (SELECT VALUE fn::row(id) + {
            matched: ->moves[WHERE string::lowercase(out.name) = $j
                AND ($a IS NONE OR string::lowercase(action) = $a)
                AND ($kind IS NONE OR kind = $kind)].{ kind, action } }
        FROM exercise
        WHERE ->moves[WHERE string::lowercase(out.name) = $j
            AND ($a IS NONE OR string::lowercase(action) = $a)
            AND ($kind IS NONE OR kind = $kind)] != []
        LIMIT $limit ?? 50);
};

DEFINE FUNCTION OVERWRITE fn::similar($ex: record<exercise>, $limit: option<int>) -> array {
    LET $n = $limit ?? 10;
    LET $vec = $ex.embedding;
    IF $vec IS NONE { RETURN [] };
    -- knn() is only populated as a plain projected field, so select it first, then shape rows.
    LET $hits = (SELECT id, vector::distance::knn() AS d FROM exercise
        WHERE embedding <|51,80|> $vec ORDER BY d);
    RETURN $hits.filter(|$h| $h.id != $ex).slice(0, $n)
        .map(|$h| fn::row($h.id) + { distance: math::fixed($h.d, 4) });
};

DEFINE FUNCTION OVERWRITE fn::values($field: string) -> array {
    RETURN IF $field = 'sections' { (SELECT VALUE section FROM exercise GROUP BY section) }
        ELSE IF $field = 'equipment' { (SELECT VALUE name FROM equipment ORDER BY name) }
        ELSE IF $field = 'muscles' { (SELECT VALUE name FROM muscle ORDER BY name) }
        ELSE IF $field = 'joints' { (SELECT VALUE name FROM joint ORDER BY name) }
        ELSE IF $field = 'roles' { (SELECT VALUE role FROM works GROUP BY role) }
        ELSE IF $field = 'actions' { (SELECT VALUE action FROM moves GROUP BY action) }
        ELSE { [] };
};

-- HTTP layer -----------------------------------------------------------------
DEFINE FUNCTION OVERWRITE fn::require_key($req: object, $next: function) -> object {
    IF $api_key IS NONE OR $req.headers["x-api-key"] != $api_key {
        RETURN { status: 401, body: { error: "missing or invalid x-api-key" } };
    };
    $next($req)
};

DEFINE FUNCTION OVERWRITE fn::int_param($v: any, $default: int) -> int {
    RETURN IF $v IS NONE { $default } ELSE { math::min([math::max([<int> $v, 1]), 200]) };
};

DEFINE API OVERWRITE "/search"
    FOR get
        MIDDLEWARE api::res::body("json"), fn::require_key()
        THEN {
            LET $p = $request.query;
            IF $p.q IS NONE { RETURN { status: 400, body: { error: "q is required" } } };
            { status: 200, body: fn::search($p.q, NONE, $p.section, $p.equipment, $p.muscle,
                $p.role, fn::int_param($p.limit, 10)) }
        }
    FOR post
        MIDDLEWARE api::res::body("json"), api::req::max_body("64kb"), api::req::body("json"), fn::require_key()
        THEN {
            LET $b = $request.body;
            IF $b.q IS NONE { RETURN { status: 400, body: { error: "q is required" } } };
            { status: 200, body: fn::search($b.q, $b.vector, $b.section, $b.equipment, $b.muscle,
                $b.role, fn::int_param($b.limit, 10)) }
        };

DEFINE API OVERWRITE "/exercise/:id"
    FOR get
        MIDDLEWARE api::res::body("json"), fn::require_key()
        THEN {
            LET $e = type::record("exercise", $request.params.id);
            IF $e.name IS NONE { RETURN { status: 404, body: { error: "no such exercise" } } };
            { status: 200, body: fn::card($e) }
        };

DEFINE API OVERWRITE "/muscle/:name"
    FOR get
        MIDDLEWARE api::res::body("json"), fn::require_key()
        THEN {
            { status: 200, body: fn::muscle_exercises($request.params.name, $request.query.role,
                fn::int_param($request.query.limit, 50)) }
        };

DEFINE API OVERWRITE "/joint/:joint"
    FOR get
        MIDDLEWARE api::res::body("json"), fn::require_key()
        THEN {
            { status: 200, body: fn::joint_exercises($request.params.joint, $request.query.action,
                $request.query.kind, fn::int_param($request.query.limit, 50)) }
        };

DEFINE API OVERWRITE "/similar/:id"
    FOR get
        MIDDLEWARE api::res::body("json"), fn::require_key()
        THEN {
            { status: 200, body: fn::similar(type::record("exercise", $request.params.id),
                fn::int_param($request.query.limit, 10)) }
        };

DEFINE API OVERWRITE "/values/:field"
    FOR get
        MIDDLEWARE api::res::body("json"), fn::require_key()
        THEN { { status: 200, body: fn::values($request.params.field) } };
