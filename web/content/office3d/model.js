// Stylised Three.js model of the office, hand-built from the real-space video and the approved V3 floor plan.
// Source: final_rendering_effect.html (neon scene V5). Approximate layout, not a measured reconstruction.
//
// Contract: export build(THREE) -> THREE.Group in app space (meters, Y up, floor y=0).
// Named child groups (used by scene.json and spots.json): street_window, platform_column, platform_red_curtain,
// platform_railing, trapezoid_table, screen_and_lectern, window_curved_sofa, folding_chair_<x>_<z>.
export function build(THREE) {
const room=new THREE.Group();const cyan=0x5ee6d0,lime=0xc6ff3d;
function line(points,color=cyan,opacity=.6){const geo=new THREE.BufferGeometry().setFromPoints(points.map(p=>new THREE.Vector3(...p)));const obj=new THREE.Line(geo,new THREE.LineBasicMaterial({color,transparent:true,opacity}));room.add(obj);return obj}
function box(x,y,z,w,h,d,color=0x1b3028,edge=cyan,op=1){const g=new THREE.BoxGeometry(w,h,d),m=new THREE.MeshStandardMaterial({color,roughness:.75,transparent:op<1,opacity:op,depthWrite:op>=1});const mesh=new THREE.Mesh(g,m);mesh.position.set(x,y+h/2,z);room.add(mesh);if(edge){const e=new THREE.LineSegments(new THREE.EdgesGeometry(g),new THREE.LineBasicMaterial({color:edge,transparent:true,opacity:.48}));e.position.copy(mesh.position);room.add(e)}return mesh}
function cylinder(x,y,z,r,h,color=0x263f35){const m=new THREE.Mesh(new THREE.CylinderGeometry(r,r,h,24),new THREE.MeshStandardMaterial({color,roughness:.6}));m.position.set(x,y+h/2,z);room.add(m);return m}
function tube(points,r=.022,color=cyan,opacity=1){const c=new THREE.CatmullRomCurve3(points.map(p=>new THREE.Vector3(...p)));const m=new THREE.Mesh(new THREE.TubeGeometry(c,Math.max(32,points.length*3),r,6,false),new THREE.MeshBasicMaterial({color,transparent:opacity<1,opacity}));room.add(m);return m}
// Layout coordinates derive directly from the approved V3 floor plan.
const map=(x,z)=>[(x-745)/100,(z-620)/100];
const floor=box(0,-.21,0,12.3,.2,9,0x10201b,cyan);
for(let x=-6.15;x<=6.15;x+=.5)line([[x,.005,-4.5],[x,.005,4.5]],cyan,.06);
for(let z=-4.5;z<=4.5;z+=.5)line([[-6.15,.005,z],[6.15,.005,z]],cyan,.06);
function capture(name,fn){const before=room.children.length;fn();const group=new THREE.Group();group.name=name;const parts=room.children.slice(before);for(const p of parts)group.add(p);room.add(group);return group}
function circ(x,y,z,r,color=cyan,opacity=.7){return line(Array.from({length:65},(_,i)=>[x+r*Math.cos(i/64*Math.PI*2),y,z+r*Math.sin(i/64*Math.PI*2)]),color,opacity)}
function rod(a,b,r=.018,c=0xa7bab1){return tube([a,b],r,c)}
function oriented(x,z,angle,fn){const g=capture('furniture',()=>fn());g.rotation.y=angle;g.position.set(x,0,z);return g}
function stool(x,z,angle=0,red=false){return oriented(x,z,angle,()=>{box(0,.8,0,.43,.065,.43,red?0x963f4f:0x81978b,cyan);if(!red){box(0,.85,-.2,.43,.27,.045,0x81978b,cyan);for(let y=.89;y<1.1;y+=.065)line([[-.19,y,-.225],[.19,y,-.225]],cyan,.45)}for(let dx of [-.17,.17])for(let dz of [-.17,.17])rod([dx*.9,.8,dz*.9],[dx*1.3,0,dz*1.3]);for(let z1 of [-.21,.21])rod([-.21,.25,z1],[.21,.25,z1]);rod([-.21,.25,-.21],[-.21,.25,.21]);rod([.21,.25,-.21],[.21,.25,.21])})}
function foldingChair(x,z){return oriented(x,z,-Math.PI/2,()=>{box(0,.43,0,.43,.055,.43,0x95aaa0,cyan);box(0,.65,.19,.43,.23,.045,0x95aaa0,cyan);for(let dx of [-.19,.19]){rod([dx,0,-.25],[dx,.88,.2],.014);rod([dx,0,.26],[dx,.43,-.18],.014)}rod([-.19,.13,.2],[.19,.13,.2],.012)})}
function roundTable(x,z,r=.55){cylinder(x,1.03,z,r,.065,0x756049);circ(x,1.10,z,r,cyan,.85);cylinder(x,.07,z,.045,.96,0x8faca1);for(let a of [0,Math.PI/2,Math.PI,Math.PI*1.5])rod([x,.08,z],[x+.39*Math.cos(a),.04,z+.39*Math.sin(a)],.025)}
// Phone booths along the left wall; glass doors face the room (+X).
for(const z of [.69,2.71])oriented(-5.2,z,Math.PI/2,()=>{const w=1.55,d=1.25,h=2.35;box(0,0,0,w,.07,d,0x66736a,cyan);box(0,h,0,w,.07,d,0x66736a,cyan);box(-w/2+.055,.07,0,.11,h,d,0x596d61,cyan);box(w/2-.055,.07,0,.11,h,d,0x596d61,cyan);box(0,.07,-d/2+.025,w,h,.05,0x344b40,cyan);box(0,.08,d/2,w-.2,h-.1,.018,0x408b79,cyan,.09);for(let dx of [-.65,.65])rod([dx,.08,.64],[dx,2.34,.64],.028,0x9bc6b7);rod([.45,.9,.67],[.45,1.26,.67],.027,lime);box(0,.95,-.15,1.05,.05,.45,0x6b6752,cyan);box(0,.48,.14,.42,.06,.42,0x688574,cyan);rod([0,0,.14],[0,.48,.14],.03);box(0,2.30,0,1.1,.012,.75,0x9ac9b1,0,.6)});
// Left high-back booth sofa, upholstered panels + dark headrest, as seen in video.
oriented(-3.15,-1.3,0,()=>{box(0,.16,0,2.05,.36,1.05,0x8c8e7d,cyan);box(0,.5,-.46,2.05,.83,.16,0x777f72,cyan);box(0,1.31,-.46,2.05,.17,.18,0x405f53,cyan);for(let dx of [-.95,.95])box(dx,.4,0,.15,.91,1.05,0x777f72,cyan);for(let x of [-.32,.32])line([[x,.54,-.37],[x,1.3,-.37]],cyan,.4);for(let x of [-.8,.8])rod([x,0,.3],[x,.17,.3],.035)});
// Circular raised platform, red carpet and a genuine rising annular ramp.
const pc={x:.05,z:-2.23,r:2,h:.58};cylinder(pc.x,0,pc.z,pc.r,pc.h,0x492f37);cylinder(pc.x,pc.h,pc.z,pc.r,.025,0x8d3e4e);circ(pc.x,.61,pc.z,pc.r,0xdd7a84,.8);
function ramp(t,r=2.21){const a=(405-204*t)*Math.PI/180;return [pc.x+r*Math.cos(a),.025+pc.h*t,pc.z+r*Math.sin(a)]}
const verts=[],inds=[];for(let i=0;i<=100;i++){for(let r of [2.025,2.36])verts.push(...ramp(i/100,r));if(i<100){let k=i*2;inds.push(k,k+2,k+1,k+1,k+2,k+3)}}
let rg=new THREE.BufferGeometry();rg.setAttribute('position',new THREE.Float32BufferAttribute(verts,3));rg.setIndex(inds);rg.computeVertexNormals();room.add(new THREE.Mesh(rg,new THREE.MeshStandardMaterial({color:0x6f3844,roughness:.9,side:THREE.DoubleSide})));
capture('platform_railing',()=>{for(let radius of [2.025,2.36]){for(let h of [.48,.95])tube(Array.from({length:81},(_,i)=>{const p=ramp(i/80,radius);p[1]+=h;return p}),.027,cyan,.95);for(let i=0;i<=17;i++){const p=ramp(i/17,radius);rod(p,[p[0],p[1]+.95,p[2]],.023);cylinder(p[0],p[1],p[2],.075,.02,0x97a99d)}}});
// Central metal-clad column and compact furniture on the platform.
const column=capture('platform_column',()=>{cylinder(.83,.61,-2.23,.34,2.75,0x6d8276);circ(.83,3.36,-2.23,.34,cyan,.8);for(let a=0;a<32;a++){let t=a/32*Math.PI*2;line([[.83+.345*Math.cos(t),.63,-2.23+.345*Math.sin(t)],[.83+.345*Math.cos(t),3.33,-2.23+.345*Math.sin(t)]],cyan,.13)}});
const platformFurniture=capture('platform_furniture',()=>{box(-.35,1.35,-2.25,1.45,.07,.65,0x756049,cyan);for(let dx of [-.92,.23])rod([dx,.61,-2.25],[dx,1.35,-2.25],.035);for(let x of [-.7,-.15]){cylinder(x,1.05,-1.62,.24,.08,0x943e4f);rod([x,.61,-1.62],[x,1.05,-1.62],.025);box(x,1.15,-1.43,.43,.35,.06,0x61766b,cyan)}});
// Small red curtain section is located on the platform, not on the street facade.
const curtain=capture('platform_red_curtain',()=>{for(let i=0;i<13;i++){const a=(242+i*2.3)*Math.PI/180;const x=pc.x+1.75*Math.cos(a),z=pc.z+1.75*Math.sin(a);cylinder(x,.61,z,.055,2.4,i%2?0x7c3041:0x9e3b50)} });
// Upper-right high table with two red-topped stools.
roundTable(4.03,-2.98,.51);stool(4.03,-3.78,Math.PI,true);stool(4.83,-2.75,-Math.PI/2,true);
// Lower-left round table and exactly five white high chairs, per approved plan.
// Trapezoid: parallel edges run along Z, parallel to the screen's horizontal span.
const tableOutline=[[-2.42,.95],[-1.28,1.30],[-1.28,2.70],[-2.42,3.05]];
const trapezoidTable=capture('trapezoid_table',()=>{
 const shape=new THREE.Shape();tableOutline.forEach((p,i)=>i?shape.lineTo(p[0],-p[1]):shape.moveTo(p[0],-p[1]));shape.closePath();
 const geo=new THREE.ExtrudeGeometry(shape,{depth:.07,bevelEnabled:false});const mesh=new THREE.Mesh(geo,new THREE.MeshStandardMaterial({color:0x756049,roughness:.7}));mesh.rotation.x=-Math.PI/2;mesh.position.y=1.03;room.add(mesh);
 line([...tableOutline,tableOutline[0]].map(p=>[p[0],1.10,p[1]]),cyan,.9);
 for(const [x,z] of [[-2.30,1.12],[-1.40,1.44],[-1.40,2.56],[-2.30,2.88]])rod([x,.05,z],[x,1.03,z],.028);
});for(let [x,z] of [[-2.85,1.45],[-.88,1.55],[-2.85,2.65],[-.88,2.55],[-1.85,3.32]])stool(x,z,Math.atan2(-1.85-x,2-z),false);
// Nine folding chairs in a 3 x 3 grid, facing the platform.
for(let z of [.95,2.03,3.11])for(let x of [1,1.94,2.88])foldingChair(x,z).name=`folding_chair_${x}_${z}`;
// Correction from annotated photo: right wall is the presentation end, not a sofa.
const presentation=capture('screen_and_lectern',()=>{
 box(6.01,0,1.1,.06,3.3,5.1,0x243c32,cyan,.14);
 box(5.94,1.05,.85,.09,2.08,3.65,0x172c25,cyan);
 box(5.88,1.13,.85,.018,1.92,3.45,0xb2c6b6,cyan);
 // The red horizontal accent recalls the real projected event slide.
 box(5.86,2.65,.78,.014,.12,2.7,0xbc3d52,0);
 for(let i=0;i<3;i++)box(5.86,2.25-i*.21,.2,.014,.045,1.4-i*.25,0x4b7260,0);
 box(5.0,.08,2.8,.65,.08,.6,0x45594e,cyan);
 box(5.0,.16,2.8,.14,.75,.14,0x50665a,cyan);
 box(5.0,.7,2.8,.5,.46,.48,0x1d3228,cyan);
 box(5.0,1.16,2.8,.68,.06,.6,0x3f564a,cyan);
 rod([5.02,1.22,2.75],[4.92,1.43,2.75],.012,cyan);
});
// Curved upholstered seating belongs under the street windows (+Z).
const sofa=capture('window_curved_sofa',()=>{for(let i=0;i<12;i++){
 const t=(i+.5)/12,x=.55+3.95*t,z=3.91+.23*Math.sin(Math.PI*t);
 const angle=Math.atan2(.23*Math.PI*Math.cos(Math.PI*t),3.95);
 oriented(x,z,-angle,()=>{
 box(0,.15,0,.36,.37,.55,0x899283,cyan);
 box(0,.50,.23,.36,.77,.13,0x747f70,cyan);
 box(0,1.27,.23,.36,.14,.15,0x3b5d4c,cyan);
 rod([0,0,0],[0,.15,0],.025);
 });
}});
// Street-facing facade is the BOTTOM edge in plan; it has the entrance gap.
const facade=capture('street_window',()=>{for(let [x1,x2] of [[-6.15,-4],[-2.15,6.15]]){box((x1+x2)/2,.02,4.5,x2-x1,2.9,.018,0x458c88,0,.025);for(let x=x1;x<=x2+.01;x+=1.04){line([[x,0,4.5],[x,2.9,4.5]],cyan,.20);for(let z of [1.1,1.4])box(x+.22,z,4.5,.11,.11,.014,0x99374d,0,.5)}line([[x1,2.9,4.5],[x2,2.9,4.5]],cyan,.22);tube([[x1,.025,4.5],[x2,.025,4.5]],.027,0x5baede)}});
// Entrance marker and subtle boundary silhouettes.
line([[-6.15,0,4.5],[-6.15,0,-4.5],[6.15,0,-4.5],[6.15,0,4.5]],cyan,.65);
tube([[-3.075,.04,5.15],[-3.075,.04,4.6]],.025,lime);tube([[-3.25,.04,4.85],[-3.075,.04,4.6],[-2.9,.04,4.85]],.025,lime);
return room;
}
