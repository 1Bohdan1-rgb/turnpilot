// The page's three.js and its one WebGL renderer (a browser allows only a few contexts): every 3D view on a page (the
// part, the shop) renders with it and copies its picture into its own 2D canvas. three.js is a file of the project
// (static/vendor/three): no internet needed; it is loaded only when a view asks for it.

let three = null;
export function loadThree() {
  if (!three) three = Promise.all([import("three"), import("/static/vendor/three/OrbitControls.js")]);
  return three;
}

let shared = null;
export function sharedRenderer(THREE) {
  if (!shared) shared = new THREE.WebGLRenderer({antialias: true, preserveDrawingBuffer: true});
  return shared;
}
