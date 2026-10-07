/* English for the map page. Thai is the page's own language and stays the default; this file only does something when the
   visitor picked English (localStorage "nkb-lang" = "en", set by the Language buttons in the layers panel).
   It translates the text the page and app.js put on screen: exact phrases, a few number-bearing sentence patterns, and the
   77 province names. Station names, advice sentences from the server, the help, privacy and report pages stay in Thai.
   Nothing here changes how the page works: it only rewrites text nodes and aria-label/title/placeholder/alt attributes,
   and rewrites them again whenever app.js draws something new. */
(function(){
"use strict";
var lang = null;
try{ lang = localStorage.getItem("nkb-lang"); }catch(e){}
function setLang(v){ try{ if(v==="en") localStorage.setItem("nkb-lang","en"); else localStorage.removeItem("nkb-lang"); }catch(e){} location.reload(); }
function wire(){
  var seg = document.getElementById("langSeg"); if(!seg) return;
  Array.prototype.forEach.call(seg.querySelectorAll("button[data-lang-set]"), function(b){
    b.setAttribute("aria-pressed", String((b.getAttribute("data-lang-set")==="en") === (lang==="en")));
    b.addEventListener("click", function(){ setLang(b.getAttribute("data-lang-set")); });
  });
}
wire();
if(lang!=="en") return;
document.documentElement.setAttribute("lang","en");

var PROVINCES = {"กรุงเทพมหานคร":"Bangkok","กระบี่":"Krabi","กาญจนบุรี":"Kanchanaburi","กาฬสินธุ์":"Kalasin","กำแพงเพชร":"Kamphaeng Phet","ขอนแก่น":"Khon Kaen","จันทบุรี":"Chanthaburi","ฉะเชิงเทรา":"Chachoengsao","ชลบุรี":"Chon Buri","ชัยนาท":"Chai Nat","ชัยภูมิ":"Chaiyaphum","ชุมพร":"Chumphon","เชียงราย":"Chiang Rai","เชียงใหม่":"Chiang Mai","ตรัง":"Trang","ตราด":"Trat","ตาก":"Tak","นครนายก":"Nakhon Nayok","นครปฐม":"Nakhon Pathom","นครพนม":"Nakhon Phanom","นครราชสีมา":"Nakhon Ratchasima","นครศรีธรรมราช":"Nakhon Si Thammarat","นครสวรรค์":"Nakhon Sawan","นนทบุรี":"Nonthaburi","นราธิวาส":"Narathiwat","น่าน":"Nan","บึงกาฬ":"Bueng Kan","บุรีรัมย์":"Buri Ram","ปทุมธานี":"Pathum Thani","ประจวบคีรีขันธ์":"Prachuap Khiri Khan","ปราจีนบุรี":"Prachin Buri","ปัตตานี":"Pattani","พระนครศรีอยุธยา":"Phra Nakhon Si Ayutthaya","พะเยา":"Phayao","พังงา":"Phangnga","พัทลุง":"Phatthalung","พิจิตร":"Phichit","พิษณุโลก":"Phitsanulok","เพชรบุรี":"Phetchaburi","เพชรบูรณ์":"Phetchabun","แพร่":"Phrae","ภูเก็ต":"Phuket","มหาสารคาม":"Maha Sarakham","มุกดาหาร":"Mukdahan","แม่ฮ่องสอน":"Mae Hong Son","ยโสธร":"Yasothon","ยะลา":"Yala","ร้อยเอ็ด":"Roi Et","ระนอง":"Ranong","ระยอง":"Rayong","ราชบุรี":"Ratchaburi","ลพบุรี":"Lop Buri","ลำปาง":"Lampang","ลำพูน":"Lamphun","เลย":"Loei","ศรีสะเกษ":"Si Sa Ket","สกลนคร":"Sakon Nakhon","สงขลา":"Songkhla","สตูล":"Satun","สมุทรปราการ":"Samut Prakan","สมุทรสงคราม":"Samut Songkhram","สมุทรสาคร":"Samut Sakhon","สระแก้ว":"Sa Kaeo","สระบุรี":"Saraburi","สิงห์บุรี":"Sing Buri","สุโขทัย":"Sukhothai","สุพรรณบุรี":"Suphan Buri","สุราษฎร์ธานี":"Surat Thani","สุรินทร์":"Surin","หนองคาย":"Nong Khai","หนองบัวลำภู":"Nong Bua Lam Phu","อ่างทอง":"Ang Thong","อำนาจเจริญ":"Amnat Charoen","อุดรธานี":"Udon Thani","อุตรดิตถ์":"Uttaradit","อุทัยธานี":"Uthai Thani","อุบลราชธานี":"Ubon Ratchathani"};
var MONTHS = {"ม.ค.":"Jan","ก.พ.":"Feb","มี.ค.":"Mar","เม.ย.":"Apr","พ.ค.":"May","มิ.ย.":"Jun","ก.ค.":"Jul","ส.ค.":"Aug","ก.ย.":"Sep","ต.ค.":"Oct","พ.ย.":"Nov","ธ.ค.":"Dec"};
var EXACT = {
 /* status words (the whole app uses these) */
 "ปกติ":"Normal","เฝ้าระวัง":"Watch","เตือนภัย":"Alert","ไม่มีเกณฑ์เทียบ":"No threshold","ไม่มีเกณฑ์":"No threshold","ไม่มีข้อมูล":"No data",
 "กำลังสูงขึ้น":"Rising","กำลังลดลง":"Falling","ทรงตัว":"Steady","ล้นตลิ่ง":"Over the bank","ล้นตลิ่งแล้ว":"Over the bank","ใกล้ล้นตลิ่ง":"Near the bank top",
 "น้ำสูงใกล้ตลิ่ง":"Water close to the bank top","ยังต่ำกว่าตลิ่ง":"Still below the bank","น้ำเสมอระดับตลิ่ง":"Water level with the bank",
 /* top bar, panels, buttons */
 "น้ำใกล้บ้านฉัน":"Water Near Me","ชั้นแผนที่":"Layers","อำเภอ":"District","ตำบล":"Subdistrict","อำเภอ / ตำบล":"District / subdistrict",
 "โหมดสี":"Colour mode","ตามเครื่อง":"Device default","สว่าง":"Light","มืด":"Dark","ติดตั้งบนหน้าจอหลัก":"Install on the home screen","ติดตั้งแอปน้ำใกล้บ้านฉัน":"Install Water Near Me",
 "บน iPhone/iPad: แตะปุ่มแชร์ (สี่เหลี่ยมมีลูกศรชี้ขึ้น) ที่ Safari แล้วเลือก \"เพิ่มลงในหน้าจอโฮม\" จะได้ไอคอนเปิดได้เร็วเหมือนแอป":"On iPhone/iPad: tap the Share button in Safari (the square with an arrow pointing up), then choose \"Add to Home Screen\" for an app-like icon.",
 "เลเยอร์":"Layers","เส้นเชื่อมสถานีตามแม่น้ำ":"Lines linking stations along rivers",
 "เป็นเส้นตรงระหว่างสถานี ไม่ใช่แนวแม่น้ำจริง เรียงต้นน้ำ→ปลายน้ำจากระดับท้องน้ำ (ประมาณ)":"Straight lines between stations, not the real river course. Upstream to downstream is estimated from river-bed level.",
 "พื้นที่น้ำท่วมจากดาวเทียม (GISTDA)":"Flooded areas from satellite (GISTDA)","ภาพถ่ายดาวเทียม อาจล่าช้าและถูกเมฆบัง ไม่ใช่ประกาศทางการ":"Satellite imagery. It can be delayed or blocked by cloud, and it is not an official announcement.",
 "ย้อนหลัง 1 วัน":"Last 1 day","3 วัน":"3 days","7 วัน":"7 days","30 วัน":"30 days",
 "ศูนย์พักพิงชั่วคราว (ปภ.)":"Temporary shelters (DDPM)","แสดงตามพื้นที่ที่เลือก (จังหวัด / อำเภอ / ตำบล) ข้อมูลอาจไม่เป็นปัจจุบัน โทรสอบถามก่อนเดินทาง":"Shown for the selected area (province / district / subdistrict). The data may be out of date, so call before you travel.",
 "เรดาร์ฝน (ย้อนหลัง 2 ชม.)":"Rain radar (last 2 hours)","ภาพจาก RainViewer ความละเอียดต่ำ ใช้ดูทิศทางฝนคร่าวๆ":"Images from RainViewer at low resolution. Good for the rough direction of the rain.",
 "▶ เล่น":"▶ Play","⏸ หยุด":"⏸ Pause","จุดน้ำท่วมที่ผู้ใช้รายงาน":"Flooding reported by users","ข้อมูลจากผู้ใช้ ยังไม่ผ่านการตรวจสอบ แสดง 12 ชม. ล่าสุด":"From users and not verified. Shows the last 12 hours.",
 "ตำแหน่งของฉัน":"My location","ติดตามตำแหน่งขณะเดินหรือขับรถ":"Follow my position while walking or driving",
 "จุดสีฟ้าขยับตามคุณ ใช้เฉพาะตอนเปิดหน้านี้ ไม่ส่งตำแหน่งไปเก็บที่ใด (สถานีใกล้เคียงจะอัปเดตเมื่อคุณขยับไปไกลเกิน 1 กม.) กินแบตเพิ่ม ปิดได้ทุกเมื่อ":"The blue dot moves with you. It works only while this page is open and your position is not sent anywhere. Nearby stations update when you move more than 1 km. It uses extra battery and can be turned off any time.",
 "ปักหมุดบ้านฉัน":"Pin my home","ยกเลิกการปักหมุด":"Cancel pinning","ล้างตำแหน่ง":"Clear location",
 "สัญลักษณ์บนแผนที่ (ตัวเลข = % ของตลิ่ง)":"Map symbols (the number = % of the bank)","ปกติ (<70%) วงกลมเล็ก สีเขียว":"Normal (<70%): small green circle",
 "เฝ้าระวัง (70–90%) สี่เหลี่ยมมน สีเหลือง":"Watch (70–90%): yellow rounded square","เตือนภัย (≥90%) วงกลมใหญ่ สีแดง มีวงกระเพื่อม":"Alert (≥90%): large red circle with a ripple",
 "ไม่มีเกณฑ์เทียบ / ไม่มีข้อมูลในช่วงนั้น":"No threshold / no data in that period","เส้นประรอบจุด = ข้อมูลเก่าเกิน 2 ชม.":"Dashed ring = data older than 2 hours","พื้นดำขอบสี = กลุ่มสถานี ตัวเลขคือ":"Dark centre with a coloured rim = a group of stations; the number is the",
 "จำนวนสถานี":"number of stations","(ไม่ใช่ %) สีขอบคือสถานะที่แรงสุดในกลุ่ม แตะเพื่อซูมเข้า":"(not a %). The rim colour is the most severe status in the group. Tap to zoom in.",
 "แจ้งจุดน้ำท่วม":"Report flooding","ช่วยคนแถวนั้นรู้ว่าตรงไหนท่วมอยู่ ข้อมูลจะแสดงบนแผนที่ 12 ชม. โดยไม่เก็บชื่อหรือรูป":"Help people nearby see where it is flooded. Reports show on the map for 12 hours and no name or photo is stored.",
 "ใช้ตำแหน่งปัจจุบัน":"Use my current location","เลือกบนแผนที่":"Pick on the map","ยังไม่ได้เลือกตำแหน่ง":"No location chosen yet","เปียกแฉะ":"Soggy","ถึงข้อเท้า":"Ankle-deep","ถึงเข่า":"Knee-deep","เกินเอว":"Above the waist",
 "ยกเลิก":"Cancel","ส่งรายงาน":"Send report","ไม่ใช่ประกาศทางการ ถ้าอยู่ในอันตรายโทร 1784 (ปภ.) หรือ 191":"This is not an official announcement. If you are in danger call 1784 (DDPM) or 191.",
 "เช็กเส้นทาง":"Check a route","ดูว่าเส้นทางขับรถผ่านจุดน้ำท่วมที่ผู้ใช้รายงาน หรือใกล้สถานีเฝ้าระวัง/เตือนภัยหรือไม่":"See whether a driving route passes user-reported flooding or goes near stations on watch or alert.",
 "ต้นทาง":"From","ตำแหน่งฉัน":"My location","ปลายทาง":"To","ล้างค่า":"Clear","ตรวจเส้นทาง":"Check route",
 "คำนวณเส้นทางด้วย OSRM และค้นหาสถานที่ด้วย OpenStreetMap (ต้นทางและปลายทางที่พิมพ์จะถูกส่งไปยังบริการเหล่านั้น) ผลเป็นการประเมินคร่าวๆ ไม่ได้รับประกันว่าผ่านได้ ถ้าไม่แน่ใจโทร 1784 หรือ 191":"Routes are calculated with OSRM and places are searched with OpenStreetMap (the start and end you type are sent to those services). The result is a rough estimate and does not guarantee a route is passable. If unsure, call 1784 or 191.",
 "เลือกพื้นที่":"Choose an area","ล้างพื้นที่":"Clear area","เสร็จ":"Done","สถานการณ์น้ำ":"Water situation","กำลังโหลด…":"Loading…","🔊 ฟัง":"🔊 Listen","⏹ หยุด":"⏹ Stop","วิธีใช้":"How to use","รายงาน →":"Report →",
 "รับแจ้งเตือนทาง LINE":"Get alerts on LINE","ส่งตำแหน่งบ้านให้บอต แล้วรับแจ้งเมื่อน้ำใกล้บ้านเปลี่ยนสถานะ และสรุปทุกเช้า 07:00":"Send your home location to the bot to be told when the water near home changes status, plus a summary every morning at 07:00.","เพิ่มเพื่อน":"Add friend",
 "ศูนย์พักพิงใกล้คุณ":"Shelters near you","ข้อมูลจาก ปภ. (ปรับปรุงล่าสุด พ.ค. 2567) อาจไม่เป็นปัจจุบัน โทรสอบถามก่อนเดินทาง":"From DDPM (last updated May 2024). It may be out of date, so call before you travel.",
 "เบอร์ฉุกเฉิน (โทรฟรี)":"Emergency numbers (free)","ปภ. แจ้งภัย/ขอความช่วยเหลือ":"DDPM: report a disaster / ask for help","การแพทย์ฉุกเฉิน":"Medical emergency","แจ้งเหตุด่วนเหตุร้าย":"Police emergency",
 "กรมชลประทาน ข้อมูลน้ำ/เขื่อน":"Royal Irrigation Dept: water and dam information","กรมอุตุฯ สภาพอากาศ":"Meteorological Dept: weather","คู่มือการใช้งาน":"User guide","นโยบายความเป็นส่วนตัว":"Privacy policy",
 "· ข้อมูลระดับน้ำจาก ThaiWater (สสน.) ใช้ประกอบการตัดสินใจเท่านั้น ไม่ใช่ประกาศทางการ ให้ยึดคำสั่งของ ปภ. ในพื้นที่เป็นหลัก (สายด่วน 1784) · การค้นหาสถานที่ใช้ Nominatim ของ OpenStreetMap (คำที่ค้นหาจะถูกส่งไปยังบริการนั้น) · แผนที่ © OpenStreetMap contributors · ศูนย์พักพิง: ปภ. (Open Data Common) · เรดาร์: RainViewer · เส้นทาง: OSRM · พยากรณ์ฝน: Open-Meteo · ขอบเขตพื้นที่: UN OCHA / กรมแผนที่ทหาร (CC BY-IGO)":"· Water-level data from ThaiWater (HII). For decision support only; not an official announcement. Follow DDPM instructions in your area first (hotline 1784) · Place search uses OpenStreetMap Nominatim (what you search is sent to that service) · Map © OpenStreetMap contributors · Shelters: DDPM (Open Data Common) · Radar: RainViewer · Routes: OSRM · Rain forecast: Open-Meteo · Boundaries: UN OCHA / Royal Thai Survey Department (CC BY-IGO)",
 "แผนที่สถานีวัดน้ำ":"Water-station map","ค้นหาสถานี จังหวัด หรือสถานที่":"Search stations, places","ล้างคำค้น":"Clear search","ชื่อที่แนะนำ":"Suggested names","ใกล้ฉัน ใช้ตำแหน่งปัจจุบัน":"Near me (use current location)","ใกล้ฉัน":"Near me",
 "โหมดสี ตามเครื่อง แตะเพื่อเปลี่ยน":"Colour mode: device default. Tap to change.","ชั้นแผนที่ เปิดปิดน้ำท่วมดาวเทียม เรดาร์ฝน ศูนย์พักพิง":"Map layers: satellite floods, rain radar, shelters","เลือกจังหวัด":"Choose a province","เลือกอำเภอหรือเขต":"Choose a district","เลือกตำบลหรือแขวง":"Choose a subdistrict",
 "ตัวเลือกแผนที่":"Map options","ช่วงเวลาของภาพดาวเทียม":"Satellite image period","ระดับน้ำ":"Water level","หมายเหตุสั้นๆ (ไม่บังคับ) เช่น ถนนหน้าตลาด รถเล็กผ่านไม่ได้":"A short note (optional), e.g. road by the market, small cars cannot pass","หมายเหตุ":"Note",
 "ชื่อสถานที่ เช่น ตลาดบางปะอิน":"A place name, e.g. Bang Pa-in market","ชื่อสถานที่ เช่น สยามพารากอน":"A place name, e.g. Siam Paragon","ขยายหรือย่อแผงข้อมูล":"Expand or collapse the panel","เรียงลำดับสถานี":"Sort stations",
 /* summary, lists, cards */
 "ทั้งประเทศ":"All of Thailand","ยังไม่มีข้อมูลสถานี":"No station data yet","ระดับน้ำปกติ":"Water levels normal"," ทุกสถานีที่มีเกณฑ์เทียบ":" at every station with a threshold","ทุกสถานีที่มีเกณฑ์เทียบ":"at every station with a threshold",
 "ยังประเมินไม่ได้ (ไม่มีเกณฑ์เทียบ)":"Cannot assess yet (no thresholds)","% ของตลิ่ง = ระดับน้ำเทียบกับความสูงตลิ่ง 100% คือน้ำเสมอตลิ่ง":"% of bank = water level against bank height. 100% means the water is level with the bank.",
 "น้ำขึ้นเร็ว":"Rising fast","ล้นตลิ่งมาก":"Far over the bank","สูงสุด":"Highest","น้ำสูงสุด":"Highest water","ใกล้คุณที่สุด":"Nearest to you","ล้นตลิ่งมากที่สุด":"Most over the bank","น้ำขึ้นเร็วที่สุด":"Rising fastest",
 "ไม่มีข้อมูล 3 ชม.":"No data for 3 h","ไม่มีสถานีที่น้ำขึ้นเร็ว":"No stations rising fast","▲ น้ำขึ้นเร็วใน 3 ชม.ที่ผ่านมา":"▲ Rising fast in the last 3 hours","นับจากระดับน้ำจริงของสถานี ไม่นับค่าที่กระโดดผิดปกติ":"Counted from real station levels; abnormal jumps are ignored.",
 "ต้นน้ำ (ถ้าสูงขึ้น ปลายน้ำมักตามมา)":"Upstream (if it rises, downstream usually follows)","ปลายน้ำ":"Downstream","เทียบกราฟกับสถานีนี้":"Compare chart with this station","แชร์ลิงก์สถานีนี้":"Share a link to this station","แชร์เป็นภาพ":"Share as image","แชร์ลิงก์จุดนี้":"Share a link to this point",
 "สถานีใกล้ที่สุด 3 แห่ง":"3 nearest stations","จุดที่แชร์":"Shared point","บ้านของฉัน":"My home","บ้านของคุณ":"Your home","ตำแหน่งของคุณ":"Your location","พื้นที่ที่เลือก":"Selected area","ใช้ตำแหน่งปัจจุบันของคุณ":"Using your current location",
 "แตะเพื่อใช้ตำแหน่งปัจจุบันของคุณ":"Tap to use your current location","กำลังหาตำแหน่งของคุณ…":"Finding your location…","กำลังหาตำแหน่ง…":"Finding location…","กำลังติดตามตำแหน่งของคุณ…":"Following your location…","เบราว์เซอร์นี้ไม่รองรับการระบุตำแหน่ง":"This browser cannot get your location",
 "ติดตามตำแหน่งไม่ได้ กรุณาอนุญาตการเข้าถึงตำแหน่ง":"Cannot follow your location. Please allow location access.","เรียกข้อมูลสถานีใกล้เคียงไม่ได้":"Could not load nearby stations","แตะบนแผนที่ตรงจุดที่ตั้งบ้านของคุณ":"Tap the map where your home is","ย้ายหมุดบ้าน":"Move the home pin","แตะบนแผนที่ตรงจุดที่น้ำท่วม":"Tap the map where it is flooded",
 "ข้อมูลอาจล่าช้า":"Data may be delayed","ระบบยังดึงข้อมูลจาก ThaiWater ไม่สำเร็จ":"The system has not been able to fetch data from ThaiWater yet","ตรวจกับ ปภ. สายด่วน 1784":"Check with DDPM hotline 1784","ตัวเลขที่เห็นอาจไม่ใช่ค่าปัจจุบัน ถ้าต้องตัดสินใจ โปรดตรวจกับ ปภ. โทร 1784":"The figures may not be current. If you must decide something, check with DDPM, call 1784.",
 "เชื่อมต่อไม่ได้ แสดงข้อมูลที่บันทึกไว้":"No connection. Showing saved data.","กำลังอัปเดต แสดงข้อมูลที่บันทึกไว้ในเครื่องก่อน":"Updating. Showing data saved on this device first.","ตัวเลขใหม่จะมาแทนที่เมื่อโหลดเสร็จ":"New figures will replace these when loading finishes.",
 "คัดลอกลิงก์แล้ว":"Link copied","คัดลอกลิงก์แล้ว (มีพิกัดโดยประมาณของจุดนี้)":"Link copied (it contains the approximate coordinates of this point)","แชร์ไม่สำเร็จ":"Could not share","กำลังสร้างภาพ…":"Creating the image…","บันทึกภาพแล้ว (ส่งต่อได้จากแกลเลอรี)":"Image saved (share it from your gallery)","สร้างภาพไม่ได้ ลองใหม่อีกครั้ง":"Could not create the image. Try again.",
 "ไม่มีฝน":"No rain","ฝนเล็กน้อย":"Light rain","ฝนปานกลาง":"Moderate rain","ฝนหนัก":"Heavy rain","ฝนหนักมาก":"Very heavy rain","ตอนนี้":"Now","วันนี้":"Today","พรุ่งนี้":"Tomorrow","มะรืนนี้":"Day after tomorrow","เมื่อสักครู่":"Just now",
 "ปริมาณฝนรายชั่วโมงใน 24 ชั่วโมงข้างหน้า":"Hourly rain over the next 24 hours","กำลังโหลดค่าจากเครื่องวัดฝน…":"Loading rain-gauge readings…","ไม่มีเครื่องวัดฝนในรัศมี 25 กม. ที่รายงานเมื่อไม่นานมานี้":"No rain gauge within 25 km has reported recently","เครื่องวัดฝนใกล้คุณ (ฝน 24 ชม. ที่วัดได้จริง)":"Rain gauges near you (24 h rain actually measured)",
 "ช่วง 24 ชม. ข้างหน้าแทบไม่มีฝน":"Hardly any rain in the next 24 hours","ศูนย์พักพิง":"Shelters","เลือกจังหวัดเพื่อดูศูนย์พักพิง":"Choose a province to see shelters","ไม่มีข้อมูลศูนย์พักพิงของจังหวัดนี้":"No shelter data for this province","โหลดข้อมูลศูนย์พักพิงไม่ได้":"Could not load shelter data","ยังไม่มีภาพเรดาร์ฝน":"No radar images yet","โหลดเรดาร์ฝนไม่ได้":"Could not load the rain radar",
 "ขอบคุณที่แจ้ง":"Thank you for reporting","ส่งไม่สำเร็จ":"Could not send","ส่งไม่สำเร็จ ตรวจข้อมูลแล้วลองใหม่":"Could not send. Check the details and try again.","ขอบคุณ ส่งรายงานแล้ว จะแสดงบนแผนที่ 12 ชม.":"Thank you. Your report is sent and will show on the map for 12 hours.","เชื่อมต่อไม่ได้ ลองใหม่อีกครั้ง":"No connection. Try again.","ยังไม่ผ่านการตรวจสอบ":"Not verified",
 "กรอกต้นทางและปลายทางก่อน":"Enter a start and a destination first","กำลังตรวจ…":"Checking…","หาเส้นทางระหว่างสองจุดนี้ไม่ได้":"No route found between these two points","คำนวณเส้นทางไม่ได้ในขณะนี้ ลองใหม่อีกครั้ง":"Cannot calculate the route right now. Try again.","ลบเส้นทางที่ตรวจไว้ออกจากแผนที่":"Remove the checked route from the map",
 "ติดตั้งแล้ว เปิดได้จากหน้าจอหลัก":"Installed. Open it from the home screen.","กำลังติดตั้ง…":"Installing…","กรุงเทพมหานคร ":"Bangkok ","เขต":"District","แขวง":"Subdistrict","ดาวเทียม":"Satellite","น้ำท่วมจากดาวเทียม":"Satellite floods","ฝน":"Rain","เรดาร์ฝน":"Rain radar","พักพิง":"Shelters",
 "สถานี":"Stations","ล่าสุด":"Latest","จังหวัดนี้":"this province","อำเภอนี้":"this district","ตำบลนี้":"this subdistrict","โหลดข้อมูลเปรียบเทียบไม่ได้":"Could not load comparison data","กราฟเทียบเปอร์เซ็นต์ของตลิ่งสองสถานี":"Chart comparing % of bank at two stations",
 "▲ สูงขึ้น":"▲ rising","▼ ลดลง":"▼ falling","ข้อมูลไม่อัปเดต":"data not updating","⚠ สองแหล่งขัดกัน":"⚠ two sources disagree",
 "⚠ สถานีนี้ข้อมูลไม่อัปเดต ใช้ประกอบอย่างระวัง":"⚠ This station's data is not updating. Use it with care.","ไม่ใช่ประกาศทางการ ยึด ปภ. 1784":"Not an official announcement. Follow DDPM 1784."
};
var PATTERNS = [
 [/^(\d+) สถานี$/, function(m){ return m[1]+(m[1]==="1" ? " station" : " stations"); }],
 [/^(\d+) สถานี · อัปเดต (.+)$/, function(m){ return m[1]+(m[1]==="1" ? " station" : " stations")+" · updated "+date(m[2]); }],
 [/^เตือนภัย: น้ำสูงใกล้หรือล้นตลิ่ง (\d+) จาก (\d+) สถานี$/, function(m){ return "Alert: water near or over the bank at "+m[1]+" of "+m[2]+" stations"; }],
 [/^เฝ้าระวัง: น้ำเริ่มสูง (\d+) จาก (\d+) สถานี$/, function(m){ return "Watch: water starting to rise at "+m[1]+" of "+m[2]+" stations"; }],
 [/^([+\-−]?\d+(?:\.\d+)?) ซม\.$/, function(m){ return m[1]+" cm"; }],
 [/^(ปกติ|เฝ้าระวัง|เตือนภัย|ล้นตลิ่ง|ไม่มีเกณฑ์|ไม่มีข้อมูล) (\d+(?:\.\d+)?)%$/, function(m){ return EXACT[m[1]]+" "+m[2]+"%"; }],
 [/^ข้อมูลค้าง (\d+)$/, function(m){ return "Stale data "+m[1]; }],
 [/^น้ำสูงสุดใน (.+) \((\d+) สถานี\)$/, function(m){ return "Highest water in "+place(m[1])+" ("+m[2]+(m[2]==="1" ? " station)" : " stations)"); }],
 [/^(.+) \((\d+)\)$/, function(m){ var t = m[1]==="ทั้งประเทศ" ? "All of Thailand" : PROVINCES[m[1]]; return t ? t+" ("+m[2]+")" : null; }],
 [/^เตือนภัย (\d+)$/, function(m){ return "Alert "+m[1]; }],
 [/^เฝ้าระวัง (\d+)$/, function(m){ return "Watch "+m[1]; }],
 [/^ปกติ (\d+)$/, function(m){ return "Normal "+m[1]; }],
 [/^▲ กำลังสูงขึ้น (\d+) สถานี$/, function(m){ return "▲ Rising at "+m[1]+" stations"; }],
 [/^ข้อมูลค้าง (\d+) สถานี$/, function(m){ return m[1]+" stations with stale data"; }],
 [/^(\d+) นาทีที่แล้ว$/, function(m){ return m[1]+" min ago"; }],
 [/^(\d+) ชม\. ที่แล้ว$/, function(m){ return m[1]+" h ago"; }],
 [/^(\d+) วันที่แล้ว$/, function(m){ return m[1]+" days ago"; }],
 [/^ของตลิ่ง \((.+)\) ที่ (.+)$/, function(m){ return "of bank ("+(EXACT[m[1]]||m[1])+") at "+m[2]; }],
 [/^(.*)% ของตลิ่ง$/, function(m){ return m[1]+"% of bank"; }],
 [/^ของตลิ่ง$/, function(){ return "of bank"; }],
 [/^สูงกว่าตลิ่งราว (\d+) ซม\.$/, function(m){ return "About "+m[1]+" cm above the bank"; }],
 [/^ต่ำกว่าตลิ่งราว (\d+) ซม\.$/, function(m){ return "About "+m[1]+" cm below the bank"; }],
 [/^(\d+(?:\.\d+)?) กม\.$/, function(m){ return m[1]+" km"; }],
 [/^ห่าง (\d+(?:\.\d+)?) กม\.(.*)$/, function(m){ return m[1]+" km away"+m[2]; }],
 [/^(\d+) มม\.$/, function(m){ return m[1]+" mm"; }],
 [/^(\d+) มม\. ใน 24 ชม\.$/, function(m){ return m[1]+" mm in 24 h"; }],
 [/^ใน 24 ชม\. · (.+) · โอกาสฝนสูงสุด (\d+)%$/, function(m){ return "in 24 h · "+(EXACT[m[1]]||m[1])+" · max chance of rain "+m[2]+"%"; }],
 [/^โอกาส (\d+)%$/, function(m){ return "Chance "+m[1]+"%"; }],
 [/^พยากรณ์ฝน · (.+)$/, function(m){ return "Rain forecast · "+place(m[1]); }],
 [/^จ\.(.+)$/, function(m){ return place("จ."+m[1]); }],
 [/^ล้นตลิ่งแล้ว (\d+) สถานี$/, function(m){ return "Over the bank at "+m[1]+" stations"; }],
 [/^น้ำขึ้นเร็ว (\d+) แห่งใน 3 ชม\.$/, function(m){ return m[1]+" stations rising fast in 3 h"; }],
 [/^ศูนย์พักพิง: (.+)$/, function(m){ return "Shelter: "+m[1]; }],
 [/^ผู้ใช้รายงาน: (.+)$/, function(m){ return "User report: "+m[1]; }],
 [/^สถานการณ์น้ำ ณ (.+)$/, function(m){ return "Water situation, "+place(m[1]); }],
 [/^น้ำใกล้บ้านฉัน · (.+)$/, function(m){ return "Water Near Me · "+place(m[1]); }],
 [/^น้ำใกล้บ้านฉัน – (.+)$/, function(m){ return "Water Near Me – "+place(m[1]); }],
 [/^บันทึกเมื่อ (.+)$/, function(m){ return "Saved at "+m[1]; }],
 [/^ชั้นแผนที่ เปิดอยู่ (\d+) ชั้น (.*)$/, function(m){ return "Map layers: "+m[1]+" on. Satellite floods, rain radar, shelters"; }],
 [/^แบตเตอรี่เหลือ (\d+)%(.*)$/, function(m){ return "Battery at "+m[1]+"%."+" Satellite and radar layers are off to save power."; }],
 [/^โหมดสี: (.+)$/, function(m){ return "Colour mode: "+(EXACT[m[1]]||m[1]); }]
];
function place(s){
  var t = s.replace(/^จ\./,"").replace(/^จังหวัด/,"");
  if(PROVINCES[t]) return PROVINCES[t]+(/^จ\./.test(s)||/^จังหวัด/.test(s) ? " Province" : "");
  return s;
}
function date(s){ return s.replace(/(\d{1,2})\s+([ก-๙.]+)/, function(all,d,mo){ return MONTHS[mo] ? MONTHS[mo]+" "+d : all; }).replace(/\s*น\.$/,""); }
function trCore(core){
  var out = EXACT[core];
  if(out===undefined && PROVINCES[core]) out = PROVINCES[core];
  if(out===undefined) for(var i=0;i<PATTERNS.length && out===undefined;i++){ var m = PATTERNS[i][0].exec(core); if(m){ var r = PATTERNS[i][1](m); if(r!==null) out = r; } }
  if(out===undefined && core.indexOf(" · ")>0){   // "ปทุมธานี · ▲ สูงขึ้น": each part on its own; parts we do not know stay as they are
    var changed = false, parts = core.split(" · ").map(function(p){ var t = trCore(p); if(t!==null){ changed = true; return t; } return p; });
    if(changed) out = parts.join(" · ");
  }
  return out===undefined ? null : out;
}
function tr(s){
  if(!/[฀-๿]/.test(s)) return null;
  var lead = s.match(/^\s*/)[0], trail = s.match(/\s*$/)[0], core = s.replace(/\s+/g," ").trim();
  if(!core) return null;
  var out = trCore(core);
  return out===null ? null : lead+out+trail;
}
var ATTRS = ["aria-label","title","placeholder","alt"];
function skip(el){ return !!(el && el.closest && el.closest("[data-i18n-skip],script,style")); }
function node(n){
  if(n.nodeType===3){
    if(skip(n.parentNode)) return;
    var t = tr(n.nodeValue); if(t!==null && t!==n.nodeValue) n.nodeValue = t;
  } else if(n.nodeType===1){
    if(skip(n)) return;
    ATTRS.forEach(function(a){ var v = n.getAttribute(a); if(v){ var t = tr(v); if(t!==null && t!==v) n.setAttribute(a,t); } });
    for(var c = n.firstChild; c; c = c.nextSibling) node(c);
  }
}
function run(){
  node(document.documentElement);
  new MutationObserver(function(list){
    list.forEach(function(r){
      if(r.type==="characterData") node(r.target);
      else if(r.type==="attributes") node(r.target);
      else Array.prototype.forEach.call(r.addedNodes, node);
    });
  }).observe(document.documentElement, {childList:true, subtree:true, characterData:true, attributes:true, attributeFilter:ATTRS});
}
run();
})();
