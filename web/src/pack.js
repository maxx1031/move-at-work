// Content pack loader. The only module that fetches JSON.
//
// In:  content/<packId>/{scene,spots,exercises,session}.json
// Out: Pack = { id, scene, spots, exercises, stops, reminder, sessionId }
//      stops[i] = { n, title, durationSec, spot, exercise }  (joined, in route order)

const FILES = ["scene", "spots", "exercises", "session"];

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

export async function loadPack(id) {
  const base = `content/${id}/`;
  const [scene, spotDoc, exDoc, session] = await Promise.all(FILES.map((f) => getJson(`${base}${f}.json`)));

  if (spotDoc.sceneId !== scene.sceneId) console.warn(`spots.json is for "${spotDoc.sceneId}", scene is "${scene.sceneId}"`);
  if (scene.topdown) scene.topdown.href = base + scene.topdown.file;
  scene.objects ??= [];

  const exercises = exDoc.exercises.map((e) => (e.media ? { ...e, media: { ...e.media, src: base + e.media.src } } : e));
  const spotById = new Map(spotDoc.spots.map((s) => [s.id, s]));
  const exById = new Map(exercises.map((e) => [e.id, e]));

  const stops = session.stops.map((st, i) => {
    const spot = spotById.get(st.spotId), exercise = exById.get(st.exerciseId);
    if (!spot) throw new Error(`session.json stop ${i + 1}: unknown spotId "${st.spotId}"`);
    if (!exercise) throw new Error(`session.json stop ${i + 1}: unknown exerciseId "${st.exerciseId}"`);
    return { n: i + 1, title: st.title ?? exercise.name, durationSec: st.durationSec ?? exercise.durationSec, spot, exercise };
  });

  return { id, scene, spots: spotDoc.spots, exercises, stops, reminder: session.reminder, sessionId: session.id };
}

// Overlay user-uploaded media (see media.js) onto a pack. Pure; returns a new pack.
// urls: { "spot:<id>": objectURL (photo), "exercise:<id>": objectURL (video) }
export function withMedia(pack, urls) {
  const spot = (s) => (urls[`spot:${s.id}`] ? { ...s, photo: urls[`spot:${s.id}`] } : s);
  const exercise = (e) => {
    const src = urls[`exercise:${e.id}`];
    return src ? { ...e, media: { type: "video", src, caption: "Your video", uploaded: true } } : e;
  };
  return {
    ...pack,
    spots: pack.spots.map(spot),
    exercises: pack.exercises.map(exercise),
    stops: pack.stops.map((st) => ({ ...st, spot: spot(st.spot), exercise: exercise(st.exercise) })),
  };
}
