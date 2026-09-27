// =====================================================================
//  Grindsläppare – automatisk släppare för bandgrind (t.ex. Flexigera)
// =====================================================================
//
//  Tidsstyrd släppmekanism som håller grindhandtagets krok/ögla och
//  släpper den när en mekanisk äggklocka når noll. Rullens fjäder drar
//  sedan in bandet.
//
//  Princip: tre steg som vart och ett minskar kraften kraftigt.
//    1. KROKARM  – handtaget hakas på en rostfri M6-bult ("fingret") som
//                  också leder stängselströmmen. Bandet vill vrida armen
//                  moturs; armens svans trycker med en kullagerrulle mot…
//    2. SPÄRR    – vars nos tar emot rullen nästan rakt mot spärrens axel.
//                  Spärren vill släppa (moturs) med ett litet moment och
//                  hålls i sin tur med en rulle i spetsen av…
//    3. BLOCKERARE – vars nos tar emot rullen med kraften nästan genom
//                  blockerarens axel (liten "hållande" offset). Äggklockans
//                  finger lyfter blockerarens flagga → allt släpper.
//
//  Koordinater: plattan ligger i XY-planet mot stolpen (Z=0 mot stolpen,
//  +Z ut mot dig). +Y = uppåt. Bandet drar åt -X (vänster sett framifrån).
//  Sitter bandet på andra sidan: sätt spegla = true.
//
//  Enheter: mm, N, Nmm.
// =====================================================================

/* [Visa] */
// Vilken del som visas/exporteras
del = "montage"; // [montage, bottenplatta, krokarm, sparr, blockerare, klockkopp, fingerring]
// Visa montaget i utlöst läge
utlost = false;
// Spegla hela konstruktionen (bandet kommer från höger)
spegla = false;

/* [Last] */
// Bandets dragkraft i handtaget [N] (bara för kraftberäkningen i konsolen)
band_kraft = 40;

/* [Krokarm] */
// Axel till överkant av bultblocket
krok_L = 55;
// Axel till svansens rulle (längre = mindre kraft på spärren)
krok_r = 50;
// Armens bredd
krok_b = 16;
// Bultblockets bredd, höjd (längs armen) och tjocklek
block_b = 22;
block_h = 24;
block_t = 16;
// Fingerbult (M6 rostfri)
finger_d = 6;
// Bultskallens nyckelvidd och höjd (M6 sexkant = 10 / 4)
skalle_nv = 10;
skalle_h = 4;
// Extra höjd för kabelsko under skallen
kabelsko_t = 2;

/* [Rullar och axlar] */
// Kullagerrulle, t.ex. 623ZZ = 10 x 4 (3 mm hål)
rulle_d = 10;
rulle_h = 4;
// Hål för M3-skruv till rullen (självgängande i plasten)
rulle_skruv_hal = 2.8;
// Axlar (M4-skruvar med mutter i plattans baksida)
axel_d = 4;
axel_spel = 0.25;
// Navradie runt axlarna
nav_r = 6;
// Tjocklek på hävarmarna
arm_t = 8;
// Spalt platta–arm (bricka)
spalt = 1;

/* [Spärr (steg 2)] */
// Avstånd rullkontakt → spärrens axel längs kraftlinjen
s2 = 12;
// Offset från kraftlinjen, > 0 = spärren vill släppa. Med rulle räcker ~1–2
e2 = 1.5;
// Spärrens armlängd (axel → spetsrulle)
sparr_L = 50;
// Hur långt nosen når förbi kontaktpunkten (mindre = mindre vridning för att släppa)
nos_a = 2;
// Hur långt nosen fortsätter på andra sidan kontaktpunkten
nos_b = 4;
// Spärrens maximala vridning när den släppt [grader]
sparr_stopp = 28;

/* [Blockerare (steg 3)] */
// Avstånd rullkontakt → blockerarens axel
s3 = 14;
// Offset, < 0 = blockeraren håller (trycks mot sitt stopp). -0.5 … -1.5
e3 = -0.8;
// Blockerarens armbredd
blk_b = 10;
// Blockerarens armlängd till flaggan, 0 = automatiskt efter klockan
blk_L_manuell = 0;
// Blockerarens maximala vridning [grader]
blk_stopp = 30;

/* [Äggklocka] */
// Klockans kroppsdiameter (där den står i koppen)
klocka_d = 60;
// Diameter på den del som vrids (där fingerringen kläms fast)
klocka_topp_d = 58;
// Klockans totala höjd
klocka_h = 45;
// Avstånd från klockans ovansida till ringens överkant
ring_fran_topp = 0;
// Ringens höjd
ring_h = 8;
// Går klockan moturs (sett uppifrån/framifrån) när den räknar ned? De flesta gör det.
klocka_moturs = true;
// Koppens väggtjocklek och djup
kopp_vagg = 2.4;
kopp_djup = 15;

/* [Bottenplatta] */
platta_t = 6;
// Skruvhål för montering (t.ex. 4,5 mm träskruv)
skruv_d = 4.5;
// Marginal runt mekanismen
marginal = 10;

/* [Upplösning] */
$fn = 64;

// ---------------------------------------------------------------------
//  Hjälpfunktioner
// ---------------------------------------------------------------------
function rot(v, a) = [v[0]*cos(a) - v[1]*sin(a), v[0]*sin(a) + v[1]*cos(a)];
function unit(v) = v / norm(v);
function rot_om(p, c, a) = c + rot(p - c, a);

// ---------------------------------------------------------------------
//  Härledd geometri (allt i "låst" läge)
// ---------------------------------------------------------------------
rr = rulle_d / 2;
spets_r = rr - 0.6;               // armände under rullen: mindre än rullen
z_arm = platta_t + spalt;         // armarnas underkant
z_arm_topp = z_arm + arm_t;
nos_h = arm_t + 0.5 + rulle_h + 0.5;   // nosen täcker både arm- och rullskiktet

// Steg 1 – krokarm
P1 = [0, 0];
T1 = [0, -krok_r];                // svansrulle
t1 = [1, 0];                      // svansens rörelseriktning när armen släpper
n1 = rot(t1, -45);                // kontaktnormal (rulle → nos)
q1 = rot(n1, 90);
C1 = T1 + rr * n1;
P2 = C1 + s2 * n1 + e2 * q1;      // spärrens axel

// Steg 2 – spärr
T2 = P2 + [sparr_L, 0];           // spetsrulle
t2 = [0, 1];
n3 = rot(t2, -45);
q3 = rot(n3, 90);
C3 = T2 + rr * n3;
P3 = C3 + s3 * n3 + e3 * q3;      // blockerarens axel

// Steg 3 – blockerare + äggklocka
moturs = spegla ? !klocka_moturs : klocka_moturs;
kopp_r = klocka_d/2 + 0.5 + kopp_vagg;
R_finger = kopp_r + 5;            // fingrets radie runt klockans centrum
blk_L = blk_L_manuell > 0 ? blk_L_manuell : (moturs ? 2*kopp_r + 5 : 25);
Q = P3 + [blk_L, 0];              // flagga
K = moturs ? Q - [R_finger, 0] : Q + [R_finger, 0];   // klockans centrum

z_kopp = z_arm + nos_h + 3;       // koppens undersida
z_klocka_topp = z_kopp + 3 + klocka_h;
z_ring_topp = z_klocka_topp - ring_fran_topp;
z_ben_botten = z_arm + nos_h + 2;
flagg_h = nos_h + 14;             // flaggans höjd över armens underkant

// Stoppklackar på plattan
klack_r = 3;
function hw_krok(d) = (nav_r + 2) + (spets_r - (nav_r + 2)) * d / krok_r;
function hw_sparr(d) = nav_r + (spets_r - nav_r) * d / sparr_L;
function hw_blk(d) = nav_r + (blk_b/2 - nav_r) * d / blk_L;
krok_oppen = 110;
klack_krok_vila  = P1 + [-(hw_krok(krok_r/2) + klack_r), -krok_r/2];
klack_krok_stopp = P1 + rot([hw_krok(22) + klack_r, -22], krok_oppen);
klack_sparr_vila  = P2 + [28, -(hw_sparr(28) + klack_r)];
klack_sparr_stopp = P2 + rot([28, hw_sparr(28) + klack_r], sparr_stopp);
klack_blk_vila  = P3 + [22, -(hw_blk(22) + klack_r)];
klack_blk_stopp = P3 + rot([22, hw_blk(22) + klack_r], blk_stopp);
klackar = [klack_krok_vila, klack_krok_stopp, klack_sparr_vila,
           klack_sparr_stopp, klack_blk_vila, klack_blk_stopp];

// Pelare som bär klockkoppen (under blockerarens arm)
pelare_r = 4;
pelare_vinklar = [215, 250, 290, 325];
pelare = [for (a = pelare_vinklar) K + (kopp_r - 7) * [cos(a), sin(a)]];

// Plattans yttermått
pkt = concat([P1, T1, P2, T2, P3, Q + [8, 0]], klackar, pelare);
x_min = min([for (p = pkt) p[0]]) - marginal - 4;
x_max = max([for (p = pkt) p[0]]) + marginal;
y_min = min([for (p = pkt) p[1]]) - marginal;
y_max = max([for (p = pkt) p[1]]) + marginal + 4;

// ---------------------------------------------------------------------
//  Kraftuppskattning (skrivs i konsolen)
// ---------------------------------------------------------------------
L_eff = krok_L + 8;  // handtaget vilar strax ovanför blocket
F_svans = band_kraft * L_eff / krok_r;
N2 = F_svans / cos(45);
M2 = N2 * e2;
N3 = (M2 / sparr_L) / cos(45);
M3 = N3 * abs(e3);
F_flagga = M3 / blk_L;
M_klocka = F_flagga * R_finger;
echo(str("Svanskraft ", F_svans, " N, spärrkontakt ", N2, " N"));
echo(str("Spärrens släppmoment ", M2, " Nmm → blockerarkontakt ", N3, " N"));
echo(str("Blockerarens hållmoment ", M3, " Nmm → ", F_flagga,
         " N vid flaggan ≈ ", M_klocka, " Nmm från äggklockan (+ blockerarens vikt)"));

// ---------------------------------------------------------------------
//  2D-hjälp
// ---------------------------------------------------------------------
// Nos: plan kontaktyta tangent mot rullen i C, ihopbyggd med navet i P
module nos2d(C, n, q, P) {
    hull() {
        translate(P) circle(r = nav_r);
        polygon([C + nos_a*q, C - nos_b*q, C - nos_b*q + 2*n, C + nos_a*q + 2*n]);
    }
}

module axelhal(P, h) {
    translate([P[0], P[1], -1]) cylinder(d = axel_d + 2*axel_spel, h = h + 2);
}

module rullfaste(T) {
    // liten ring som lyfter rullens innerring över armen
    translate([T[0], T[1], 0]) cylinder(r = 2.9, h = arm_t + 0.5);
}

module rullhal(T) {
    translate([T[0], T[1], -1]) cylinder(d = rulle_skruv_hal, h = arm_t + 3);
}

// ---------------------------------------------------------------------
//  Delar (ritade i världskoordinater, underkant på Z = 0)
// ---------------------------------------------------------------------
module krokarm() {
    y0 = krok_L - block_h;
    zc = block_t / 2;
    difference() {
        union() {
            linear_extrude(arm_t) hull() {
                circle(r = nav_r + 2);
                translate(T1) circle(r = spets_r);
            }
            linear_extrude(arm_t) hull() {
                circle(r = nav_r + 2);
                translate([-krok_b/2, y0]) square([krok_b, block_h]);
            }
            translate([-block_b/2, y0, 0]) cube([block_b, block_h, block_t]);
            rullfaste(T1);
        }
        axelhal(P1, block_t);
        rullhal(T1);
        // bulthål längs armen
        translate([0, y0 - 1, zc]) rotate([-90, 0, 0])
            cylinder(d = finger_d + 0.4, h = block_h + 2);
        // sexkantsficka för bultskallen + kabelsko, öppen framåt
        translate([0, y0 + 3, 0])
            hull() for (z = [zc, block_t + 1])
                translate([0, 0, z]) rotate([-90, 0, 0]) rotate([0, 0, 30])
                    cylinder(d = skalle_nv / cos(30) + 0.5, h = skalle_h + kabelsko_t + 0.5, $fn = 6);
        // kabelspår framåt/åt höger ur fickan
        translate([0, y0 + 3 + 0.5, zc])
            cube([block_b, skalle_h + kabelsko_t - 0.5, block_t]);
    }
}

module sparr() {
    difference() {
        union() {
            linear_extrude(arm_t) hull() {
                translate(P2) circle(r = nav_r);
                translate(T2) circle(r = spets_r);
            }
            linear_extrude(nos_h) nos2d(C1, n1, q1, P2);
            rullfaste(T2);
        }
        axelhal(P2, nos_h);
        rullhal(T2);
    }
}

module blockerare() {
    difference() {
        union() {
            linear_extrude(arm_t) hull() {
                translate(P3) circle(r = nav_r);
                translate(Q) circle(r = blk_b/2);
            }
            linear_extrude(nos_h) nos2d(C3, n3, q3, P3);
            // flagga (paddel) som fingret lyfter
            linear_extrude(flagg_h) hull() {
                translate(Q) circle(r = 4);
                translate(Q + [8, 0]) circle(r = 4);
            }
        }
        axelhal(P3, flagg_h);
    }
}

module bottenplatta() {
    difference() {
        union() {
            translate([x_min, y_min, 0])
                cube([x_max - x_min, y_max - y_min, platta_t]);
            // navbrickor
            for (P = [P1, P2, P3])
                translate([P[0], P[1], 0]) cylinder(r = nav_r, h = platta_t + spalt);
            // stoppklackar
            for (k = klackar)
                translate([k[0], k[1], 0]) cylinder(r = klack_r, h = z_arm_topp);
            // pelare till klockkoppen
            for (p = pelare)
                translate([p[0], p[1], 0]) cylinder(r = pelare_r, h = z_kopp);
        }
        // M4 genom plattan med sexkantsficka för mutter på baksidan
        for (P = [P1, P2, P3]) {
            axelhal(P, platta_t + spalt);
            translate([P[0], P[1], -0.01]) cylinder(d = 7 / cos(30) + 0.4, h = 3.5, $fn = 6);
        }
        // skruvhål i pelarna (M3 självgängande)
        for (p = pelare)
            translate([p[0], p[1], z_kopp - 12]) cylinder(d = 2.8, h = 13);
        // monteringshål, försänkta framifrån
        for (x = [x_min + 7, x_max - 7], y = [y_min + 7, y_max - 7]) {
            translate([x, y, -1]) cylinder(d = skruv_d, h = platta_t + 2);
            translate([x, y, platta_t - skruv_d/2]) cylinder(d1 = skruv_d, d2 = 2*skruv_d + 0.2, h = skruv_d/2 + 0.01);
        }
    }
}

module klockkopp() {
    // ritad med centrum i K, undersida på Z = 0
    translate([K[0], K[1], 0]) difference() {
        cylinder(r = kopp_r, h = 3 + kopp_djup);
        translate([0, 0, 3]) cylinder(r = kopp_r - kopp_vagg, h = kopp_djup + 1);
        for (p = pelare) {
            translate([p[0] - K[0], p[1] - K[1], -1]) cylinder(d = 3.4, h = 5);
            translate([p[0] - K[0], p[1] - K[1], 1.5]) cylinder(d1 = 3.4, d2 = 6.4, h = 1.51);
        }
        // spår för buntband tvärs över botten
        for (s = [-1, 1]) translate([s * (kopp_r - 9), 0, 1.5]) cube([4, 2*kopp_r, 10], center = true);
    }
}

module fingerring() {
    // ritad med klockans centrum i origo, ringens ovankant på Z = 0, benet nedåt (-Z)
    ri = klocka_topp_d / 2;
    ben_langd = z_ring_topp - z_ben_botten;
    difference() {
        union() {
            translate([0, 0, -ring_h]) cylinder(r = ri + 3, h = ring_h);
            // klämöron
            translate([-(ri + 9), -6, -ring_h]) cube([10, 12, ring_h]);
            // arm ut till fingret
            translate([0, -3, -5]) cube([R_finger + 3, 6, 5]);
            // ben ned mot flaggan
            translate([R_finger - 3, -3, -ben_langd]) cube([6, 6, ben_langd]);
        }
        translate([0, 0, -ring_h - 1]) cylinder(r = ri, h = ring_h + 2);
        // slits + klämskruv M3
        translate([-(ri + 10), -1, -ring_h - 1]) cube([12, 2, ring_h + 2]);
        translate([-(ri + 5), 0, -ring_h/2]) rotate([90, 0, 0]) cylinder(d = 3.2, h = 20, center = true);
    }
}

// ---------------------------------------------------------------------
//  Montage
// ---------------------------------------------------------------------
module vrid(P, a) {
    translate([P[0], P[1], 0]) rotate([0, 0, a]) translate([-P[0], -P[1], 0]) children();
}

module rulle(T) {
    color("silver") translate([T[0], T[1], z_arm_topp + 0.5]) difference() {
        cylinder(d = rulle_d, h = rulle_h);
        translate([0, 0, -1]) cylinder(d = 3, h = rulle_h + 2);
    }
}

module montage() {
    a1 = utlost ? krok_oppen : 0;
    a2 = utlost ? sparr_stopp : 0;
    a3 = utlost ? blk_stopp : 0;
    // fingrets vinkel: strax innan flaggan, eller i lyft läge
    af = (moturs ? 1 : -1) * (utlost ? 25 : -(12 / R_finger) * 180 / PI);
    fb = moturs ? 0 : 180;

    color("DarkSlateGray") bottenplatta();
    color("orange") vrid(P1, a1) translate([0, 0, z_arm]) krokarm();
    vrid(P1, a1) rulle(T1);
    // fingerbult (strömförande)
    color("gold") vrid(P1, a1) translate([0, krok_L - block_h + 3 + kabelsko_t, z_arm + block_t/2])
        rotate([-90, 0, 0]) cylinder(d = finger_d, h = block_h + 22);
    color("SteelBlue") vrid(P2, a2) translate([0, 0, z_arm]) sparr();
    vrid(P2, a2) rulle(T2);
    color("MediumSeaGreen") vrid(P3, a3) translate([0, 0, z_arm]) blockerare();
    color("Wheat") translate([0, 0, z_kopp]) klockkopp();
    color("white", 0.35) translate([K[0], K[1], z_kopp + 3]) {
        cylinder(d = klocka_d, h = klocka_h - ring_fran_topp - ring_h);
        cylinder(d = klocka_topp_d - 0.5, h = klocka_h - 0.3);
    }
    color("tomato") translate([K[0], K[1], z_ring_topp]) rotate([0, 0, fb + af]) fingerring();
}

// ---------------------------------------------------------------------
//  Utskriftsläge: varje del flyttad till origo, liggande rätt för utskrift
// ---------------------------------------------------------------------
module utskrift() {
    if (del == "bottenplatta") translate([-x_min, -y_min, 0]) bottenplatta();
    if (del == "krokarm")      krokarm();
    if (del == "sparr")        translate([-P2[0], -P2[1], 0]) sparr();
    if (del == "blockerare")   translate([-P3[0], -P3[1], 0]) blockerare();
    if (del == "klockkopp")    translate([-K[0], -K[1], 0]) klockkopp();
    if (del == "fingerring")   rotate([180, 0, 0]) fingerring();
}

mirror([spegla ? 1 : 0, 0, 0]) {
    if (del == "montage") montage();
    else utskrift();
}
