"""Self-contained HTML report — multi-pass assembly + rendering.

Extracted verbatim from chirp_analysis.py (_assemble_report, _html_report,
GLOSSARY, STRINGS). Input: a list of pass dicts from analysis.chirp.build_pass.
"""
from __future__ import annotations

import json

from .config import config_fields, config_diff
from .analysis.chirp import COHERENCE_GATE, RESIDUAL_OK_DB

MAX_OVERLAY_PASSES = 8


def _assemble_report(passes: list, lang: str = "fr") -> dict:
    """Trim to the last MAX_OVERLAY_PASSES, attach pass numbers + config diffs, mark primary."""
    shown = passes[-MAX_OVERLAY_PASSES:]
    base = len(passes) - len(shown)
    primary = len(shown) - 1
    for k, p in enumerate(shown):
        p["n"] = base + k + 1
        p["ts"] = p.get("timestamp", "").replace("T", " ")
        p["diff"] = config_diff(shown[k - 1]["config"], p["config"]) if k > 0 else ""
        # only the primary pass renders its heatmaps -> drop them from the others to keep the HTML light
        if k != primary:
            for heavy in ("spectrogram", "throttle_map", "noise_spectrum"):
                p.pop(heavy, None)
    return {"passes": shown, "primary_index": len(shown) - 1, "total_passes": len(passes),
            "lang": lang, "_glossary": GLOSSARY, "_strings": STRINGS}


GLOSSARY = {
    "chirp": {
        "fr": "Chirp : un signal sinusoïdal dont la fréquence balaie lentement du bas vers le haut "
              "(ici ~0 à 500 Hz), injecté sur la consigne d'un axe. En mesurant comment le drone y "
              "répond fréquence par fréquence, on obtient sa réponse en fréquence (Bode) — la "
              "signature dynamique complète de la boucle de stabilisation.",
        "en": "Chirp: a sine signal whose frequency slowly sweeps from low to high (here ~0 to "
              "500 Hz), injected onto an axis' setpoint. Measuring how the drone responds frequency "
              "by frequency gives its frequency response (Bode) — the full dynamic signature of the "
              "stabilisation loop.",
    },
    "gain": {
        "fr": "Gain (dB) : rapport entre le mouvement obtenu (gyro) et le mouvement demandé "
              "(consigne), en décibels. 0 dB = le drone suit exactement la demande. Au-dessus de 0 dB "
              "il en fait trop (surréaction/résonance), en dessous il atténue. Une bosse de gain = "
              "tendance à osciller à cette fréquence.",
        "en": "Gain (dB): ratio of the motion obtained (gyro) to the motion commanded (setpoint), in "
              "decibels. 0 dB = the drone tracks the command exactly. Above 0 dB it overreacts "
              "(overshoot/resonance), below it attenuates. A gain bump = tendency to oscillate there.",
    },
    "phase": {
        "fr": "Phase (°) : le retard entre la demande et la réponse, en degrés. Plus la fréquence "
              "monte, plus le retard s'accumule (filtres, inertie). Quand la phase atteint -180°, la "
              "correction arrive en opposition : si le gain est encore ≥ 0 dB à ce point, la boucle "
              "s'auto-entretient et oscille.",
        "en": "Phase (°): the lag between command and response, in degrees. The higher the frequency, "
              "the more lag accumulates (filters, inertia). When phase reaches -180°, the correction "
              "arrives in opposition: if gain is still ≥ 0 dB there, the loop self-sustains and oscillates.",
    },
    "phase_margin": {
        "fr": "Marge de phase : la réserve de stabilité (degrés avant -180°). >45° = sain et amorti ; "
              "30-45° = correct ; 15-30° = limite, ça commence à rebondir ; <15° = la boucle sonne. "
              "Classiquement lue au croisement 0 dB, mais ce point décroche sur une réponse très amortie ; "
              "le rapport reporte donc la marge GARANTIE déduite du pic de sensibilité Ms "
              "(PM ≥ 2·arcsin(1/2·Ms)) — d'où le repère f(Ms) sur les graphes, pas le croisement 0 dB. "
              "Baisser P/D ou filtrer redonne de la marge.",
        "en": "Phase margin: the stability reserve (degrees before -180°). >45° = healthy and damped; "
              "30-45° = fine; 15-30° = marginal, starts to bounce; <15° = the loop rings. Classically "
              "read at the 0 dB crossover, but that point breaks down on a very damped response; the "
              "report therefore states the GUARANTEED margin from the sensitivity peak Ms "
              "(PM ≥ 2·arcsin(1/2·Ms)) — hence the f(Ms) marker on the plots, not the 0 dB crossover. "
              "Lowering P/D or adding filtering restores margin.",
    },
    "sensitivity": {
        "fr": "Pic de sensibilité Ms : Ms = max|S(f)|, avec S = 1/(1+L) = 1−T la fonction de "
              "sensibilité (T étant la réponse boucle fermée mesurée par le chirp). C'est LE chiffre "
              "de robustesse : il borne la marge de phase par PM ≥ 2·arcsin(1/(2·Ms)). Physiquement "
              "Ms = à quel point la boucle amplifie les perturbations à sa fréquence la plus fragile "
              "f(Ms) — d'où la raie verticale. Repères : Ms ≲ 1.5 confortable et amorti ; ~2 limite ; "
              ">2 ça résonne (l'overshoot de la step monte, le propwash s'installe). Ms se baisse en "
              "redonnant de la marge (moins de P/D, ou plus de filtrage avant les PID).",
        "en": "Sensitivity peak Ms: Ms = max|S(f)|, where S = 1/(1+L) = 1−T is the sensitivity "
              "function (T being the closed-loop response the chirp measures). It is THE robustness "
              "number: it bounds the phase margin via PM ≥ 2·arcsin(1/(2·Ms)). Physically Ms is how "
              "much the loop amplifies disturbances at its most fragile frequency f(Ms) — hence the "
              "vertical marker. Rules of thumb: Ms ≲ 1.5 comfortable and damped; ~2 marginal; >2 it "
              "rings (step overshoot climbs, propwash sets in). Lower Ms by restoring margin (less "
              "P/D, or more filtering before the PIDs).",
    },
    "crossover": {
        "fr": "Crossover 0 dB : la fréquence où le gain passe sous 0 dB. C'est en gros la bande "
              "passante de l'axe — jusqu'où le drone suit fidèlement les ordres. Plus elle est haute, "
              "plus la réponse est vive, mais plus il faut de marge de phase pour rester stable.",
        "en": "0 dB crossover: the frequency where gain drops below 0 dB. Roughly the axis bandwidth — "
              "how far the drone tracks commands faithfully. Higher = sharper response, but it needs "
              "more phase margin to stay stable.",
    },
    "coherence": {
        "fr": "Cohérence (0 à 1) : à quel point la réponse mesurée est réellement causée par "
              "l'excitation chirp, et non par du bruit/vibrations. 1 = mesure fiable. En dessous de "
              "0.8 la courbe de gain/phase n'est pas fiable à cette fréquence — on l'affiche en grisé. "
              "La cohérence chute naturellement en haute fréquence.",
        "en": "Coherence (0 to 1): how much of the measured response is really caused by the chirp "
              "excitation rather than noise/vibration. 1 = trustworthy. Below 0.8 the gain/phase curve "
              "is unreliable at that frequency — shown greyed out. Coherence naturally falls at high "
              "frequency (weaker signal).",
    },
    "resonance": {
        "fr": "Résonance : un pic d'énergie marqué à une fréquence précise, dû au cadre, aux pales ou "
              "aux moteurs. Si elle remonte dans la boucle, elle fait vibrer/chauffer. On la traite par "
              "du filtrage (notch), PAS en touchant les PID — baisser les gains pour masquer une "
              "résonance dégrade le pilotage pour rien.",
        "en": "Resonance: a sharp energy peak at a specific frequency, from the frame, props or motors. "
              "If it feeds into the loop it causes vibration/heat. Treat it with filtering (a notch), "
              "NOT by changing PIDs — lowering gains to mask a resonance degrades handling for nothing.",
    },
    "filtering": {
        "fr": "Filtrage (Betaflight) : l'ensemble des filtres qui nettoient le signal du gyro AVANT que les "
              "PID ne le voient, pour que la boucle ne réagisse pas au bruit (vibrations cadre/pales/moteurs). "
              "Pourquoi : ce bruit, amplifié surtout par le terme D, repart dans les moteurs → ESC/moteurs qui "
              "chauffent, et peut entretenir des oscillations. Comment, du plus ciblé au plus large : le RPM "
              "filter (encoches calées sur le régime moteur via la télémétrie), le dynamic notch (encoches qui "
              "pistent les résonances), puis les passe-bas gyro (LPF) et D-term qui rabotent tout le haut du "
              "spectre. Le compromis central : plus on filtre, plus on enlève de bruit, mais chaque filtre "
              "ajoute du retard de phase qui grignote la marge de stabilité — d'où la règle « filtrer juste ce "
              "qu'il faut, et figer le filtrage AVANT de régler les PID ».",
        "en": "Filtering (Betaflight): the set of filters that clean the gyro signal BEFORE the PIDs see it, so "
              "the loop doesn't react to noise (frame/prop/motor vibration). Why: that noise, amplified mostly by "
              "the D term, feeds back into the motors → hot ESCs/motors, and can sustain oscillation. How, from "
              "most targeted to broadest: the RPM filter (notches locked to motor rpm via telemetry), the dynamic "
              "notch (notches tracking resonances), then the gyro and D-term lowpasses (LPF) that shave the whole "
              "top of the spectrum. The core trade-off: more filtering removes more noise, but every filter adds "
              "phase lag that eats into the stability margin — hence the rule 'filter just enough, and freeze the "
              "filtering BEFORE tuning the PIDs'.",
    },
    "gyro_lpf": {
        "fr": "Gyro lowpass (LPF) : filtre passe-bas sur le signal du gyroscope, avant tout calcul PID. "
              "Il enlève le bruit haute fréquence (moteurs/vibrations). Trop bas, il ajoute du retard de "
              "phase et déstabilise ; trop haut, il laisse passer le bruit dans les moteurs (chaleur). "
              "'dyn' = la coupure suit le throttle entre deux bornes.",
        "en": "Gyro lowpass (LPF): a lowpass filter on the gyro signal, before any PID maths. It removes "
              "high-frequency noise (motors/vibration). Too low it adds phase lag and destabilises; too "
              "high it lets noise into the motors (heat). 'dyn' = the cutoff follows throttle between two "
              "bounds.",
    },
    "dterm_lpf": {
        "fr": "D-term lowpass : filtre passe-bas sur le terme dérivé (D) des PID. Le D amplifie fortement "
              "le bruit, donc on le filtre plus que le reste. Souvent le filtre le plus critique : trop "
              "haut → moteurs chauds et bruit ; trop bas → D mou et retard qui ramène du propwash. À "
              "régler en priorité avec le RPM filter.",
        "en": "D-term lowpass: a lowpass on the PID derivative (D) term. D strongly amplifies noise, so "
              "it is filtered more than the rest. Often the most critical filter: too high → hot motors "
              "and noise; too low → mushy D and lag that brings propwash back. Tune it first, alongside "
              "the RPM filter.",
    },
    "dyn_notch": {
        "fr": "Dynamic notch : filtres très étroits qui pistent en temps réel les pics de bruit "
              "(résonances) et les coupent sans toucher au reste du spectre. 'count' = combien de pics "
              "traqués, 'Q' = finesse (Q haut = encoche étroite, moins de retard), 'min/max' = plage "
              "surveillée. C'est l'outil principal contre les résonances.",
        "en": "Dynamic notch: very narrow filters that track noise peaks (resonances) in real time and "
              "cut them without touching the rest of the spectrum. 'count' = how many peaks tracked, 'Q' "
              "= sharpness (high Q = narrow notch, less lag), 'min/max' = the watched range. The main "
              "tool against resonances.",
    },
    "rpm_filter": {
        "fr": "RPM filter : utilise la vitesse réelle des moteurs (télémétrie ESC/DShot) pour placer des "
              "encoches pile sur les harmoniques de rotation des hélices. Le filtre le plus efficace "
              "contre le bruit moteur : bien réglé, il permet d'ouvrir les autres filtres (gyro/D-term "
              "plus hauts) et donc de gagner en réactivité.",
        "en": "RPM filter: uses real motor speed (ESC/DShot telemetry) to place notches exactly on the "
              "props' rotation harmonics. The most effective filter against motor noise: when set right "
              "it lets you open the other filters (higher gyro/D-term) and so gain responsiveness.",
    },
    "dmax": {
        "fr": "D_max : valeur haute du terme D, atteinte seulement lors de mouvements brusques. Au repos "
              "le D reste à sa valeur basse (D_min, le D des PID) pour limiter le bruit ; il monte vers "
              "D_max sur les à-coups pour amortir. Si D_min = D_max, le D est fixe (pas de boost).",
        "en": "D_max: the high value of the D term, reached only on sharp moves. At rest D stays at its "
              "low value (D_min, the PID's D) to limit noise; it rises toward D_max on stick jabs to "
              "damp. If D_min = D_max, D is fixed (no boost).",
    },
    "pid": {
        "fr": "PID (P, I, D) : le cœur de la stabilisation. P = réactivité immédiate à l'erreur (trop "
              "haut = oscillation rapide) ; I = tient la consigne dans la durée et contre le vent (trop "
              "haut = rebond lent) ; D = amortit/anticipe (trop haut = bruit et chaleur). On les règle "
              "APRÈS le filtrage, car les filtres changent la marge de phase disponible.",
        "en": "PID (P, I, D): the heart of stabilisation. P = immediate reaction to error (too high = "
              "fast oscillation); I = holds the setpoint over time and against wind (too high = slow "
              "bounce); D = damps/anticipates (too high = noise and heat). Tune them AFTER filtering, "
              "because filters change the available phase margin.",
    },
    "throttle_map": {
        "fr": "Carte throttle × fréquence : spectre du gyro découpé par tranches de gaz. Les résonances "
              "moteur migrent avec le régime — une raie qui se décale en montant le gaz est d'origine "
              "moteur (RPM filter / dyn notch), une raie fixe est une résonance de cadre (notch statique).",
        "en": "Throttle × frequency map: the gyro spectrum sliced by throttle. Motor resonances migrate "
              "with rpm — a line that shifts as throttle rises is motor-borne (RPM filter / dyn notch), a "
              "fixed line is a frame resonance (static notch).",
    },
    "motor_harmonics": {
        "fr": "Harmoniques moteur : le bruit moteur se loge aux multiples de la fréquence de rotation des "
              "hélices, déduite de l'eRPM (rotation Hz = eRPM×100 / (pôles/2) / 60). Comme les 4 moteurs "
              "tournent à des régimes un peu différents et que le gaz varie, chaque harmonique (1×, 2×, 3×…) "
              "est une bande, pas une raie. Un pic de bruit DANS une bande = bruit moteur (du ressort du RPM "
              "filter / dyn_notch) ; un pic HORS bande = résonance de cadre/pale (notch statique).",
        "en": "Motor harmonics: motor noise sits at multiples of the prop rotation frequency, derived from "
              "eRPM (rotation Hz = eRPM×100 / (poles/2) / 60). Since the 4 motors run at slightly different "
              "rpm and throttle varies, each harmonic (1×, 2×, 3×…) is a band, not a line. A noise peak INSIDE "
              "a band = motor noise (RPM filter / dyn_notch territory); a peak OUTSIDE = a frame/prop "
              "resonance (static notch).",
    },
    "spectrogram": {
        "fr": "Spectrogramme : une carte temps × fréquence de l'énergie du gyro pendant le chirp. Le "
              "balayage du chirp apparaît comme une diagonale qui monte en fréquence ; une résonance "
              "apparaît comme une bande horizontale qui s'allume quand le sweep passe à sa fréquence. "
              "Sert à vérifier que le chirp a bien balayé toute la bande et à repérer visuellement les "
              "résonances et leur étalement.",
        "en": "Spectrogram: a time × frequency map of the gyro energy during the chirp. The chirp sweep "
              "shows up as a diagonal rising in frequency; a resonance shows up as a horizontal band that "
              "lights up when the sweep reaches its frequency. Use it to check the chirp actually swept the "
              "whole band and to spot resonances and their spread visually.",
    },
    "step_response": {
        "fr": "Réponse indicielle : la réaction de l'axe à un échelon de consigne, reconstruite depuis "
              "la même mesure que le Bode. C'est le pendant temporel : on y lit l'overshoot (dépassement "
              "%), le temps de montée et l'établissement. Un fort overshoot ≙ une marge de phase faible "
              "sur le Bode ; les deux courbes racontent la même histoire.",
        "en": "Step response: the axis' reaction to a step in setpoint, reconstructed from the same "
              "measurement as the Bode. It is the time-domain companion: read off overshoot (%), rise "
              "time and settling. A large overshoot ≙ a low phase margin on the Bode; both curves tell "
              "the same story.",
    },
    "noise_psd": {
        "fr": "Spectre de bruit (PSD, dB) : densité de puissance du gyro vs fréquence, mesurée hors chirp. "
              "Référence = le plancher de bruit (la base plate en haute fréquence, stable d'un vol à l'autre), "
              "donc 0 dB = plancher et un pic se lit par sa hauteur AU-DESSUS du plancher. Les deux grandeurs "
              "fiables (indépendantes de la référence) : l'atténuation brut→filtré (ce que les filtres enlèvent) "
              "et le résiduel filtré au-dessus du plancher (ce qui reste). Pas de seuil absolu type « −10 dB » : "
              "c'est arbitraire et dépendant du vol ; les vrais juges sont la marge de phase et la température moteur.",
        "en": "Noise spectrum (PSD, dB): gyro power density vs frequency, measured outside the chirp. Reference "
              "= the noise floor (the flat HF baseline, stable across flights), so 0 dB = floor and a peak is read "
              "by its height ABOVE the floor. The two reliable (reference-independent) quantities: the raw→filtered "
              "attenuation (what the filters remove) and the filtered residual above the floor (what remains). No "
              "absolute '−10 dB' threshold: it is arbitrary and flight-dependent; the real judges are phase margin "
              "and motor temperature.",
    },
    "propwash": {
        "fr": "Propwash : les oscillations/secousses quand le drone retombe dans ses propres turbulences "
              "(descentes rapides, sorties de virage). Souvent lié à un D mou ou trop filtré, ou à une "
              "marge de phase faible : la boucle n'amortit pas assez vite.",
        "en": "Propwash: the wobble/shaking when the drone falls back into its own turbulence (fast "
              "descents, corner exits). Often tied to a mushy or over-filtered D, or a low phase margin: "
              "the loop does not damp fast enough.",
    },
}


STRINGS = {
    "fr": {
        "title": "CHIRP ANALYZER", "subtitle": "analyse de réponse fréquentielle · Betaflight",
        "lang_btn": "EN", "pass_word": "Passe",
        "guide_h": "Guide de tuning",
        "pipe": "Blackbox | Identification fréquentielle | Réponse en fréquence | Phase margin / crossover | Step response simulée | Analyse bruit & filtrage | Scoring | Recommandations",
        "guide_order": "<b>Ordre recommandé :</b> on règle {filt} AVANT {pid}. Chaque filtre ajoute du retard "
                       "de {phase} qui grignote la {pm} : régler les gains avant d'avoir figé le filtrage donne "
                       "des PID qui ne tiendront plus ensuite. On nettoie donc le bruit et les {res} d'abord, "
                       "puis on monte les gains.",
        "cfg_init": "Réglages initiaux", "cfg_last": "Réglages — dernière passe", "cfg_sub": "(extraits du log)",
        "synth_h": "Lecture d'ensemble", "synth_intro": "D'après la dernière passe",
        "synth_evo": "Évolution depuis la passe 1",
        "score_h": "Note de tune", "score_vs": "vs passe précédente", "score_all": "Toutes les passes :",
        "sc_rise": "montée", "sc_margin": "marge", "sc_noise": "bruit",
        "score_cap": "Note composite 0–100 (moyenne des axes) : overshoot, montée, marge garantie, Ms et marge "
                     "au bruit, chacun ramené sur 0–100 par une courbe physique puis moyenné (montée et overshoot "
                     "pèsent le plus). Sert à dire si cette config est meilleure ou pire que la précédente — le "
                     "delta compare à la passe d'avant. À lire avec les graphes, pas à la place : une note ne "
                     "remplace pas le jugement manche en main.",
        "guide_vsag": "⚙️ Pour des passes <b>comparables</b> : active <code>vbat_sag_compensation</code> "
                      "(et/ou vole à niveau de batterie similaire). Sinon l'autorité moteur varie d'un vol à "
                      "l'autre et déplace les courbes et les marges, même sans toucher au tune.",
        "overlay_hint": "pastilles en haut à droite de chaque axe : clique une passe pour masquer/afficher ses courbes",
        "pill_off": "masquée",
        "tmap_howto": "Comment lire — chaque ligne = une tranche de gaz (ralenti en bas, plein gaz en haut), "
                      "couleur = puissance de bruit du gyro à cette fréquence. Une raie verticale qui <b>monte en "
                      "fréquence quand le gaz augmente</b> = harmonique moteur ; une raie à <b>fréquence fixe</b> "
                      "quel que soit le gaz = résonance de cadre/pale.",
        "tmap_lo": "peu de bruit", "tmap_hi": "beaucoup",
        "mapex_h": "Exemple — à quoi ressemble une MAUVAISE carte",
        "mapex_cap": "Une raie qui MONTE en fréquence avec le gaz = harmonique moteur (à traiter par RPM filter / dyn_notch). "
                     "Une raie VERTICALE à fréquence fixe = résonance de cadre/pale (notch). Une bonne carte : plancher bas "
                     "et uniforme, sans raie franche.",
        "step1_h": "Filtrage",
        "tmap_h": "Carte throttle × fréquence", "filt_h": "Pistes de filtrage",
        "tmap_none": "indisponible (ni rcCommand[3] ni motor loggés). Active le throttle/les moteurs en blackbox.",
        "noise_h": "Spectre de bruit gyro (PSD, dB)",
        "noise_cap": "{psd} — brut (gyroUnfilt) vs filtré (gyroADC), hors chirp. 0 dB = plancher de bruit ; "
                     "un pic dont le résiduel filtré retombe dans le plancher est aplati. Le repère +6 dB est "
                     "indicatif (prominence d'une raie), pas une spec — les vrais juges sont la marge de phase et "
                     "la température moteur.",
        "noise_cap_nounfilt": "{psd} — gyro filtré (gyroUnfilt absent du log). 0 dB = plancher de bruit.",
        "leg_raw": "brut (unfilt)", "leg_filt": "filtré (gyroADC)",
        "leg_floor": "plancher", "leg_resid": "résiduel (indicatif)", "leg_motor": "harmoniques moteur",
        "step2_h": "PID",
        "sanity_h": "Contrôle des mesures — balayage du chirp",
        "spectro_cap": "{sg} — gyro {ax} pendant le sweep. La diagonale qui monte = le chirp ; les bandes "
                       "horizontales = résonances qui s'allument quand le sweep les traverse.",
        "overlay": "Courbes superposées :",
        "bode_h": "Réponse en fréquence (Bode)", "step_h": "Réponse indicielle (temporel)",
        "coh_cap": "fiabilité de la mesure par fréquence (grisé si &lt; {gate})",
        "margin": "marge mesurée", "no_xover": "pas de crossover",
        "pm_gtd": "marge garantie", "bandwidth": "bande passante",
        "step3_h": "Historique & comparaison",
        "step3_single": "Une seule passe pour l'instant. Refais un log chirp après tes modifs : il s'empilera "
                        "ici pour la comparaison avant/après.",
        "step3_changes": "↳ changements vs passe précédente :",
        "evo_h": "Évolution des indicateurs par axe",
        "evo_cap": "Une vignette par axe × indicateur : les passes en abscisse, la valeur (unité rappelée "
                   "sur l'ordonnée) en y. Chaque indicateur a sa couleur + picto, repris dans la note de tune. "
                   "Survole un point pour lire sa valeur exacte. Le point = médiane ; la "
                   "moustache = l'étendue min/max inter-sweeps quand la passe a plusieurs chirps (sinon point "
                   "seul). Un trou dans la ligne = indicateur non mesurable sur cette passe. La vignette "
                   "« marge · f(Ms) » est la seule à deux courbes : marge garantie à gauche en ° (trait plein), "
                   "fréquence f(Ms) à droite en Hz (tireté). Sur le Ms, la bande verte (1,3–2) est la zone saine "
                   "visée, le rouge (>2) la zone nerveuse peu robuste.",
        "cmp_h": "Comparaison des réglages",
        "cmp_none": "Réglages PID + filtres identiques sur toutes les passes — les écarts de courbes "
                    "viennent du vol (batterie, throttle, bruit), pas du tune.",
        "glossary_h": "Glossaire",
        "w_filt": "le filtrage", "w_pid": "les PID", "w_phase": "phase",
        "w_pm": "marge de stabilité", "w_res": "résonances",
        "leg_gyro": "gyro lpf", "leg_dterm": "dterm lpf", "leg_notch": "plage dyn_notch",
        "leg_xover": "crossover 0 dB", "leg_fms": "f(Ms) — pic de sensibilité",
        "metrics": "overshoot {ov}% · montée {rise} ms · établi {settle} ms",
        "render_err": "⚠ Rendu interrompu : ",
    },
    "en": {
        "title": "CHIRP ANALYZER", "subtitle": "frequency-response analysis · Betaflight",
        "lang_btn": "FR", "pass_word": "Pass",
        "guide_h": "Tuning guide",
        "pipe": "Blackbox | Frequency identification | Frequency response | Phase margin / crossover | Simulated step response | Noise & filtering analysis | Scoring | Recommendations",
        "guide_order": "<b>Recommended order:</b> set {filt} BEFORE {pid}. Every filter adds {phase} lag that "
                       "eats into the {pm}: tuning gains before the filtering is frozen gives PIDs that won't "
                       "hold afterwards. So clean up noise and {res} first, then raise the gains.",
        "cfg_init": "Initial settings", "cfg_last": "Settings — latest pass", "cfg_sub": "(read from the log)",
        "synth_h": "Overview", "synth_intro": "Based on the latest pass",
        "synth_evo": "Change since pass 1",
        "score_h": "Tune score", "score_vs": "vs previous pass", "score_all": "All passes:",
        "sc_rise": "rise", "sc_margin": "margin", "sc_noise": "noise",
        "score_cap": "Composite 0–100 score (mean of the axes): overshoot, rise, guaranteed margin, Ms and noise "
                     "margin, each mapped to 0–100 by a physical curve then averaged (rise and overshoot weigh "
                     "most). Tells whether this config is better or worse than the previous one — the delta "
                     "compares to the pass before. Read it alongside the plots, not instead: a score is no "
                     "substitute for stick feel.",
        "guide_vsag": "⚙️ For <b>comparable</b> passes: enable <code>vbat_sag_compensation</code> (and/or fly at "
                      "a similar battery level). Otherwise motor authority varies between flights and shifts the "
                      "curves and margins even with no tune change.",
        "overlay_hint": "pills at the top-right of each axis: click a pass to hide/show its curves",
        "pill_off": "hidden",
        "tmap_howto": "How to read — each row = a throttle slice (idle at the bottom, full throttle at the top), "
                      "colour = gyro noise power at that frequency. A vertical line that <b>climbs in frequency as "
                      "throttle rises</b> = a motor harmonic; a <b>fixed-frequency</b> line at any throttle = a "
                      "frame/prop resonance.",
        "tmap_lo": "low noise", "tmap_hi": "high",
        "mapex_h": "Example — what a BAD map looks like",
        "mapex_cap": "A line that CLIMBS in frequency with throttle = a motor harmonic (handled by the RPM filter / "
                     "dyn_notch). A FIXED-frequency vertical line = a frame/prop resonance (notch). A good map: a low, "
                     "uniform floor with no sharp line.",
        "step1_h": "Filtering",
        "tmap_h": "Throttle × frequency map", "filt_h": "Filtering leads",
        "tmap_none": "unavailable (neither rcCommand[3] nor motors logged). Enable throttle/motors in blackbox.",
        "noise_h": "Gyro noise spectrum (PSD, dB)",
        "noise_cap": "{psd} — raw (gyroUnfilt) vs filtered (gyroADC), outside the chirp. 0 dB = noise floor; a "
                     "peak whose filtered residual falls back into the floor is flattened. The +6 dB line is "
                     "indicative (a line's prominence), not a spec — the real judges are phase margin and motor "
                     "temperature.",
        "noise_cap_nounfilt": "{psd} — filtered gyro (gyroUnfilt absent from the log). 0 dB = noise floor.",
        "leg_raw": "raw (unfilt)", "leg_filt": "filtered (gyroADC)",
        "leg_floor": "floor", "leg_resid": "residual (indicative)", "leg_motor": "motor harmonics",
        "step2_h": "PID",
        "sanity_h": "Measurement check — chirp sweep",
        "spectro_cap": "{sg} — {ax} gyro during the sweep. The rising diagonal = the chirp; horizontal "
                       "bands = resonances lighting up as the sweep crosses them.",
        "overlay": "Overlaid curves:",
        "bode_h": "Frequency response (Bode)", "step_h": "Step response (time domain)",
        "coh_cap": "per-frequency measurement reliability (greyed if &lt; {gate})",
        "margin": "measured margin", "no_xover": "no crossover",
        "pm_gtd": "guaranteed margin", "bandwidth": "bandwidth",
        "step3_h": "History & comparison",
        "step3_single": "Only one pass so far. Re-fly a chirp log after your changes: it will stack up here for "
                        "before/after comparison.",
        "step3_changes": "↳ changes vs previous pass:",
        "evo_h": "Per-axis indicator evolution",
        "evo_cap": "One tile per axis × indicator: passes on the x-axis, value (unit recalled on the "
                   "ordinate) on y. Each indicator has its own colour + pictogram, reused in the tune score. "
                   "Hover a point to read its exact value. The dot is the median; the whisker "
                   "is the inter-sweep min/max range when a pass has several chirps (bare dot otherwise). A gap "
                   "in the line = indicator not measurable on that pass. The 'margin · f(Ms)' tile is the only "
                   "two-curve one: guaranteed margin on the left in ° (solid), f(Ms) frequency on the right in "
                   "Hz (dashed). On Ms, the green band (1.3–2) is the healthy target zone, red (>2) the nervous, "
                   "low-robustness zone.",
        "cmp_h": "Settings comparison",
        "cmp_none": "Identical PID + filter settings across all passes — curve differences come from the "
                    "flight (battery, throttle, noise), not the tune.",
        "glossary_h": "Glossary",
        "w_filt": "filtering", "w_pid": "the PIDs", "w_phase": "phase",
        "w_pm": "stability margin", "w_res": "resonances",
        "leg_gyro": "gyro lpf", "leg_dterm": "dterm lpf", "leg_notch": "dyn_notch range",
        "leg_xover": "0 dB crossover", "leg_fms": "f(Ms) — sensitivity peak",
        "metrics": "overshoot {ov}% · rise {rise} ms · settle {settle} ms",
        "render_err": "⚠ Render interrupted: ",
    },
}


def _html_report(report: dict, file_name: str) -> str:
    payload = json.dumps(report)
    # The renderer is intentionally dependency-free: a tiny canvas plotting engine.
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Chirp report — {file_name}</title>
<style>
  body {{ font: 13px/1.5 system-ui, sans-serif; margin: 20px; background:#11141a; color:#dfe3ea; max-width:1800px; }}
  h1 {{ font-size: 19px; }} h2 {{ font-size: 15px; margin: 22px 0 8px; color:#9ecbff; }}
  .banner {{ position:relative; border-radius:10px; padding:16px 20px 14px; margin-bottom:18px; overflow:hidden;
     background:linear-gradient(120deg,#141d2a 0%,#1b2a3e 48%,#21344a 100%); border:1px solid #2c4a68; }}
  .banner::before {{ content:''; position:absolute; inset:0; pointer-events:none; opacity:.12;
     background:radial-gradient(circle at 88% 25%, #6fd0ff 0, transparent 45%); }}
  .banner-main {{ display:flex; align-items:center; gap:14px; }}
  .banner-icon {{ font-size:30px; line-height:1; color:#7fd0ff; text-shadow:0 0 14px rgba(111,208,255,.5); }}
  .banner-title {{ font-size:25px; font-weight:800; letter-spacing:2.5px; color:#eaf2fb; }}
  .banner-sub {{ font-size:12.5px; color:#9bb4cc; margin-top:1px; }}
  .banner-tags {{ margin-top:11px; }}
  .chip {{ display:inline-block; font:600 11px system-ui; letter-spacing:.4px; color:#cfe6ff; margin-right:6px;
     background:#13314e; border:1px solid #2f567d; border-radius:11px; padding:2px 10px; }}
  .banner-file {{ position:absolute; right:18px; bottom:13px; color:#8aa0b8; font-size:12px; }}
  h3 {{ font-size: 13px; color:#8893a5; margin:14px 0 4px; text-transform:uppercase; letter-spacing:.5px; }}
  .axis {{ border:1px solid #2a2f3a; border-radius:8px; padding:12px 14px 12px 17px; margin-bottom:18px; background:#171b22; position:relative; }}
  /* every block carries the same accent liseré: a vertical gradient #ff5b2e -> #2dd4ff */
  .axis::before {{ content:''; position:absolute; left:0; top:0; bottom:0; width:3px;
     border-radius:8px 0 0 8px; background:linear-gradient(180deg,#ff5b2e,#2dd4ff); }}
  .sicon {{ margin-right:7px; font-size:.95em; }}
  .passpills {{ position:absolute; top:11px; right:13px; display:flex; gap:5px; flex-wrap:wrap; justify-content:flex-end; max-width:58%; }}
  .pillbtn {{ font:600 11px system-ui; background:#0d1016; border:1.5px solid; border-radius:11px; padding:1px 9px; cursor:pointer; }}
  .pillbtn.off {{ background:transparent; border-style:dashed; text-decoration:line-through; }}
  summary.collh {{ list-style:none; cursor:pointer; font-size:13px; color:#8893a5; text-transform:uppercase;
     letter-spacing:.5px; font-weight:600; margin:14px 0 4px; }}
  summary.collh::-webkit-details-marker {{ display:none; }}
  summary.collh::before {{ content:'▸ '; color:#8893a5; }}
  details[open] > summary.collh::before {{ content:'▾ '; }}
  summary.collh2 {{ list-style:none; cursor:pointer; font-size:15px; font-weight:600; color:#9ecbff; margin:0 0 4px; }}
  summary.collh2::-webkit-details-marker {{ display:none; }}
  summary.collh2::before {{ content:'▸ '; }}
  details[open] > summary.collh2::before {{ content:'▾ '; }}
  canvas {{ display:block; background:#0d1016; border-radius:4px; margin:6px 0; }}
  .diag {{ color:#c9d2e0; }} .diag li {{ margin:2px 0; }}
  .meta {{ color:#8893a5; font-size:12px; }}
  .sugg {{ margin:8px 0 0; padding-left:18px; }} .sugg li {{ margin:3px 0; }}
  .pid {{ color:#ffd479; margin:8px 0 0; }}
  .step-d {{ color:#9cd0e0; margin:6px 0 0; }}
  .filt li {{ color:#9ce0c0; }}
  .cfg {{ color:#aab4c4; font-size:12px; line-height:1.8; }}
  .legend {{ font-size:11px; color:#8893a5; margin:0 0 6px; }}
  .legend span {{ margin-right:14px; white-space:nowrap; }}
  .guide {{ background:#141c26; border:1px solid #28425c; }}
  .guide b {{ color:#9ecbff; }}
  .score {{ background:#141c26; border:1px solid #28425c; }}
  .scoreband {{ display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; margin:2px 0 8px; }}
  .scorebig {{ font-size:40px; font-weight:700; color:#e6eaf2; line-height:1; }}
  .scoremax {{ font-size:16px; font-weight:400; color:#8893a5; }}
  .scoregrade {{ font-size:26px; font-weight:700; color:#9ecbff; }}
  .scoredelta {{ font-size:14px; font-weight:600; }}
  table.scoretab {{ border-collapse:collapse; font-size:12px; margin:6px 0; }}
  table.scoretab th, table.scoretab td {{ border:1px solid #2a2f3a; padding:3px 12px; }}
  table.scoretab th {{ text-align:center; font-weight:600; white-space:nowrap; background:#1c2330; }}
  table.scoretab td {{ color:#e6eaf2; text-align:center; }}
  table.scoretab th:first-child, table.scoretab td:first-child {{ text-align:left; }}
  .scoreall {{ margin:2px 0 4px; font-size:12.5px; }} .scoreall span {{ font-weight:600; }}
  .term {{ border-bottom:1px dotted #6b7689; cursor:help; position:relative; }}
  .term:hover::after {{ content:attr(data-tip); position:absolute; left:0; top:1.5em; z-index:20;
     width:340px; white-space:normal; background:#0b0e13; color:#e6eaf2; border:1px solid #3a4150;
     border-radius:6px; padding:9px 11px; font:12px/1.55 system-ui; box-shadow:0 6px 18px rgba(0,0,0,.55); }}
  /* pass labels carry data-pass; the rich coloured config tooltip (#htip) is shown by JS on hover */
  .passtip {{ cursor:help; }}
  .maptip {{ cursor:help; display:inline-block; width:15px; height:15px; line-height:15px; text-align:center;
     border-radius:50%; background:#28425c; color:#cfe3ff; font:bold 10px system-ui; vertical-align:middle; }}
  #htip {{ position:fixed; z-index:60; pointer-events:none; display:none; max-width:420px;
     background:#0b0e13; border:1px solid #3a5a78; border-radius:7px; padding:10px 13px;
     font:12px/1.65 ui-monospace,Consolas,monospace; box-shadow:0 8px 22px rgba(0,0,0,.6); }}
  .glos dt {{ color:#9ecbff; font-weight:600; margin-top:8px; }}
  .glos dd {{ margin:2px 0 0; color:#c2cad6; }}
  .swatch {{ display:inline-block; width:11px; height:11px; border-radius:2px; margin-right:5px; vertical-align:middle; }}
  .diff {{ color:#ffd479; }}
  table.cmp {{ border-collapse:collapse; font-size:12px; margin-top:8px; }}
  table.cmp th, table.cmp td {{ border:1px solid #2a2f3a; padding:3px 9px; text-align:left; color:#c2cad6; }}
  table.cmp th {{ color:#9ecbff; font-weight:600; }}
  table.cmp td.lbl {{ color:#8893a5; }}
  table.cmp td.chg {{ color:#ffd479; font-weight:600; background:#241f12; }}
  .passleg {{ margin:6px 0 4px; font-size:12px; color:#c2cad6; }}
  .passleg label {{ display:inline-flex; align-items:center; gap:5px; margin:2px 16px 2px 0; cursor:pointer; }}
  .passleg input {{ accent-color:#9ecbff; cursor:pointer; }}
  .howto {{ font-size:12px; color:#aab4c4; margin:4px 0 2px; }}
  /* sequence schematic: rectangular process nodes (flowchart/UML look), numbered, not header chips */
  .pipe {{ margin:10px 0 12px; line-height:2.6; }}
  .pipe b {{ display:inline-flex; align-items:center; gap:7px; vertical-align:middle;
     background:#0f1721; border:1px solid #34506e; border-left:3px solid #4fc3f7; border-radius:4px;
     padding:4px 11px; font:600 11px ui-monospace,Consolas,monospace; color:#d4e6fb; white-space:nowrap; }}
  .pipe b .nidx {{ font:700 10px ui-monospace,Consolas,monospace; color:#5f7da0; }}
  .pipe .arr {{ color:#3f6a93; margin:0 1px; font-size:14px; vertical-align:middle; }}
  .ptip {{ position:fixed; z-index:60; pointer-events:none; display:none; background:#0b0e13; color:#e6eaf2;
     border:1px solid #3a5a78; border-radius:5px; padding:3px 7px; font:11px ui-monospace,Consolas,monospace;
     box-shadow:0 4px 12px rgba(0,0,0,.55); }}
  .scalebar {{ display:inline-block; height:10px; width:120px; vertical-align:middle; margin:0 6px;
     border-radius:2px; background:linear-gradient(90deg, rgb(0,120,255), rgb(150,90,170), rgb(255,40,30)); }}
  .langbtn {{ position:fixed; top:16px; right:16px; z-index:30; background:#28425c; color:#cfe3ff;
     border:1px solid #3a5a78; border-radius:6px; padding:5px 12px; cursor:pointer; font:600 12px system-ui; }}
  .twocol {{ display:flex; gap:14px; flex-wrap:wrap; }}
  .twocol > div {{ flex:1 1 380px; }}
</style></head><body>
<button id="langbtn" class="langbtn"></button>
<div id="hdr" class="banner"></div>
<div id="root"></div>
<div id="ptip" class="ptip"></div>
<div id="htip"></div>
<script>
const FILE = {json.dumps(file_name)};
const R = {payload};
const GL = R._glossary || {{}};
const STR = R._strings || {{}};
const PASSES = R.passes || [];
const PRIMARY = R.primary_index || 0;
const PRI = PASSES[PRIMARY] || {{}};
const CFG = PRI.config || {{}};
const GATE = {COHERENCE_GATE};
const PAL = ['#7686a0','#9ad','#80cbc4','#ba9cff','#f48fb1','#aed581','#ffb74d','#4fc3f7'];
let W = 880; const Hh = 150, PAD = 46;   // W is recomputed responsively at each render()
let LANG = R.lang || 'fr';
window.addEventListener('error', e => {{
  const d=document.createElement('pre'); d.style.color='#ff8a80';
  d.textContent=(T('render_err'))+e.message+(e.lineno?(' ('+e.lineno+')'):'');
  document.body.appendChild(d);
}});
function T(k) {{ const s=STR[LANG]||STR.fr||{{}}; return (k in s)? s[k] : k; }}
function tip(k,label) {{ const g=GL[k]||{{}}; const t=(g[LANG]||g.fr||'').replace(/"/g,'&quot;');
  return '<span class="term" data-tip="'+t+'">'+(label||k)+'</span>'; }}
function loc(o) {{ return o ? (o[LANG]||o.fr||o.en||'') : ''; }}
function passLabel(p) {{ return T('pass_word')+' '+p.n+' — '+p.ts+(p.file?(' ('+p.file+')'):''); }}
function cfgFields(cfg) {{
  if (!cfg) return [];
  const o=[];
  for (const ax of ['roll','pitch','yaw']) {{ const p=(cfg.pids||{{}})[ax]; if (p) o.push([ax+' P/I/D', p.join('/')]); }}
  if (cfg.d_max) o.push(['D_max', cfg.d_max.join('/')]);
  const lpf=(key,lbl)=>{{ const d=cfg[key]||{{}}; const v=(d.dyn||d.static); if(v!=null){{ const vs=Array.isArray(v)?v.join('–'):v; o.push([lbl,(vs+' Hz '+(d.type||'')).trim()]); }} }};
  lpf('gyro_lpf1','gyro LPF1'); lpf('gyro_lpf2','gyro LPF2'); lpf('dterm_lpf1','D-term LPF1'); lpf('dterm_lpf2','D-term LPF2');
  const dn=cfg.dyn_notch||{{}}; if(dn.count!=null) o.push(['dyn_notch','×'+dn.count+' Q'+dn.q+' ['+dn.min+'–'+dn.max+' Hz]']);
  if(cfg.rpm_harmonics!=null) o.push(['RPM filter','×'+cfg.rpm_harmonics]);
  return o;
}}
function el(tag,cls,html) {{ const e=document.createElement(tag); if(cls)e.className=cls; if(html!=null)e.innerHTML=html; return e; }}
function mkCanvas(parent,h) {{ const c=document.createElement('canvas'); c.width=W; c.height=h; parent.appendChild(c); return c; }}
function lerp(v,a,b,A,B) {{ return A + (v-a)*(B-A)/((b-a)||1); }}
function logx(f,fmin,fmax) {{ return lerp(Math.log10(f), Math.log10(fmin), Math.log10(fmax), PAD, W-12); }}
function drawAxes(ctx,h,fmin,fmax,ymin,ymax,ylabel) {{
  ctx.clearRect(0,0,W,h); ctx.strokeStyle='#2a2f3a'; ctx.fillStyle='#8893a5'; ctx.font='10px sans-serif'; ctx.lineWidth=1;
  for (let k=0;k<=4;k++) {{ const yv=ymin+(ymax-ymin)*k/4, y=lerp(yv,ymin,ymax,h-22,8);
    ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-12,y); ctx.stroke();
    ctx.fillText(yv.toFixed(ymax-ymin>=10?0:1), 4, y+3); }}
  for (let d=Math.floor(Math.log10(fmin)); d<=Math.ceil(Math.log10(fmax)); d++) for (const m of [1,2,5]) {{
    const f=m*Math.pow(10,d); if (f<fmin||f>fmax) continue; const x=logx(f,fmin,fmax);
    ctx.strokeStyle='#20242e'; ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,h-22); ctx.stroke();
    ctx.fillStyle='#8893a5'; ctx.fillText(f>=1000?(f/1000)+'k':f, x-6, h-8); }}
  ctx.fillStyle='#9ecbff'; ctx.fillText(ylabel, PAD, 7);
}}
function drawAxesLin(ctx,h,xmax,ymin,ymax,ylabel,ystep,xminor) {{
  ctx.clearRect(0,0,W,h); ctx.strokeStyle='#2a2f3a'; ctx.fillStyle='#8893a5'; ctx.font='10px sans-serif'; ctx.lineWidth=1;
  if (ystep) {{ for (let yv=ymin; yv<=ymax+1e-9; yv+=ystep) {{ const y=lerp(yv,ymin,ymax,h-22,8);  // fixed 0.25 grid so 1.0 is always a line
      ctx.strokeStyle='#2a2f3a'; ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-12,y); ctx.stroke();
      ctx.fillStyle='#8893a5'; ctx.fillText(yv.toFixed(2), 4, y+3); }} }}
  else for (let k=0;k<=4;k++) {{ const yv=ymin+(ymax-ymin)*k/4, y=lerp(yv,ymin,ymax,h-22,8);
    ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-12,y); ctx.stroke(); ctx.fillText(yv.toFixed(2), 4, y+3); }}
  // faint minor x gridlines (e.g. every 10 ms) so the rise/settle timing can be gauged by eye
  if (xminor) for (let xv=xminor; xv<xmax; xv+=xminor) {{ const x=lerp(xv,0,xmax,PAD,W-12);
    ctx.strokeStyle='#23272f'; ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,h-22); ctx.stroke(); }}
  for (let k=0;k<=5;k++) {{ const xv=xmax*k/5, x=lerp(xv,0,xmax,PAD,W-12);
    ctx.strokeStyle='#3a4150'; ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,h-22); ctx.stroke();
    ctx.fillStyle='#8893a5'; ctx.fillText(xv.toFixed(0)+(k===5?' ms':''), x-6, h-8); }}
  ctx.fillStyle='#9ecbff'; ctx.fillText(ylabel, PAD, 7);
}}
function plotLine(ctx,h,F,Y,coh,fmin,fmax,ymin,ymax,color,opts) {{
  opts=opts||{{}}; const lw=opts.lw||1.8;
  for (let i=1;i<F.length;i++) {{
    const trusted = coh[i]>=GATE && coh[i-1]>=GATE;
    ctx.globalAlpha = opts.dim ? 0.5 : 1;
    ctx.strokeStyle = trusted ? color : (opts.dim?'rgba(120,130,150,0.15)':'rgba(120,130,150,0.35)');
    ctx.lineWidth = trusted ? lw : 1;
    ctx.beginPath();
    ctx.moveTo(logx(F[i-1],fmin,fmax), lerp(Y[i-1],ymin,ymax,h-22,8));
    ctx.lineTo(logx(F[i],fmin,fmax),   lerp(Y[i],ymin,ymax,h-22,8));
    ctx.stroke();
  }}
  ctx.globalAlpha=1;
}}
function plotLin(ctx,h,X,Y,xmax,ymin,ymax,color,opts) {{
  opts=opts||{{}}; ctx.globalAlpha=opts.dim?0.5:1; ctx.strokeStyle=color; ctx.lineWidth=opts.lw||1.8;
  ctx.beginPath();
  for (let i=0;i<X.length;i++) {{ const px=lerp(X[i],0,xmax,PAD,W-12), py=lerp(Y[i],ymin,ymax,h-22,8);
    i?ctx.lineTo(px,py):ctx.moveTo(px,py); }}
  ctx.stroke(); ctx.globalAlpha=1;
}}
// Inter-sweep variability band: shaded min/max envelope (lo..hi) on a log-frequency x-axis.
function plotBand(ctx,h,F,lo,hi,fmin,fmax,ymin,ymax,color) {{
  ctx.beginPath();
  for (let i=0;i<F.length;i++) {{ const x=logx(F[i],fmin,fmax),y=lerp(hi[i],ymin,ymax,h-22,8); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }}
  for (let i=F.length-1;i>=0;i--) {{ const x=logx(F[i],fmin,fmax),y=lerp(lo[i],ymin,ymax,h-22,8); ctx.lineTo(x,y); }}
  ctx.closePath(); ctx.fillStyle=color; ctx.globalAlpha=0.22; ctx.fill(); ctx.globalAlpha=1;
}}
// Same, on the linear time x-axis of the step response.
function plotBandLin(ctx,h,X,lo,hi,xmax,ymin,ymax,color) {{
  ctx.beginPath();
  for (let i=0;i<X.length;i++) {{ const x=lerp(X[i],0,xmax,PAD,W-12),y=lerp(hi[i],ymin,ymax,h-22,8); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }}
  for (let i=X.length-1;i>=0;i--) {{ const x=lerp(X[i],0,xmax,PAD,W-12),y=lerp(lo[i],ymin,ymax,h-22,8); ctx.lineTo(x,y); }}
  ctx.closePath(); ctx.fillStyle=color; ctx.globalAlpha=0.22; ctx.fill(); ctx.globalAlpha=1;
}}
// Zoomed inset (incrustation) in the lower-right of the step canvas: the first transient — x from 0 to
// when the curve comes back to 1 (~20-25 ms), y windowed around 1 (≈0.75–1.25, widened to the data) so
// the overshoot/return shape is legible without cramming the whole settle into the main plot.
function stepInset(ctx,h,sser,d,pcol) {{
  const recross=(t,y)=>{{ let pk=0; for(let i=1;i<y.length;i++) if(y[i]>y[pk]) pk=i;
    if (y[pk]>1.0) {{ for(let i=pk;i<y.length;i++) if(y[i]<=1.0) return t[i]; }}
    for(let i=0;i<y.length;i++) if(y[i]>=0.98) return t[i]; return t[t.length-1]; }};
  const prim=sser.find(o=>o.primary)||sser[sser.length-1];   // window the inset on the reference pass
  let xz=recross(prim.p.step.t_ms,prim.p.step.y)*1.3||25;
  // y-window around 1: start tracking min/max only once the curve nears the target (>=0.7), so the
  // rise from 0 doesn't drag the floor down — we want the overshoot/return detail, not the whole rise.
  let lo=0.75, hi=1.25;
  sser.forEach(o=>{{ const t=o.p.step.t_ms,y=o.p.step.y; let on=false;
    for(let i=0;i<t.length&&t[i]<=xz;i++){{ if(y[i]>=0.7) on=true; if(on){{ lo=Math.min(lo,y[i]); hi=Math.max(hi,y[i]); }} }} }});
  if (d.step.y_hi) for(let i=0;i<d.step.t_ms.length&&d.step.t_ms[i]<=xz;i++) hi=Math.max(hi,d.step.y_hi[i]);
  lo=Math.floor(lo/0.05)*0.05; hi=Math.ceil(hi/0.05)*0.05;
  const iw=(W-PAD-12)*0.40, ih=(h-30)*0.52, x0=W-12-iw-6, y0=h-22-ih-8;
  const xp=t=>x0+(t/xz)*iw, yp=v=>y0+ih-(v-lo)/(hi-lo)*ih;
  ctx.fillStyle='rgba(13,16,22,0.92)'; ctx.strokeStyle='#3a4150'; ctx.lineWidth=1;
  ctx.fillRect(x0,y0,iw,ih); ctx.strokeRect(x0,y0,iw,ih);
  ctx.save(); ctx.beginPath(); ctx.rect(x0,y0,iw,ih); ctx.clip();
  ctx.strokeStyle='#5a6273'; ctx.setLineDash([3,2]); ctx.beginPath(); ctx.moveTo(x0,yp(1)); ctx.lineTo(x0+iw,yp(1)); ctx.stroke(); ctx.setLineDash([]);
  if (d.step.y_lo && !HIDDEN.has(PRIMARY)) {{ ctx.beginPath();
    for(let i=0;i<d.step.t_ms.length&&d.step.t_ms[i]<=xz;i++){{ const x=xp(d.step.t_ms[i]),y=yp(d.step.y_hi[i]); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }}
    for(let i=d.step.t_ms.length-1;i>=0;i--){{ if(d.step.t_ms[i]>xz)continue; ctx.lineTo(xp(d.step.t_ms[i]),yp(d.step.y_lo[i])); }}
    ctx.closePath(); ctx.fillStyle=pcol; ctx.globalAlpha=0.22; ctx.fill(); ctx.globalAlpha=1; }}
  for (const o of sser) {{ ctx.globalAlpha=o.primary?1:0.5; ctx.strokeStyle=PAL[o.i%PAL.length]; ctx.lineWidth=o.primary?2:1.4;
    const t=o.p.step.t_ms,y=o.p.step.y; ctx.beginPath(); let started=false;
    for(let i=0;i<t.length&&t[i]<=xz;i++){{ const x=xp(t[i]),yy=yp(y[i]); started?ctx.lineTo(x,yy):ctx.moveTo(x,yy); started=true; }}
    ctx.stroke(); }}
  ctx.globalAlpha=1; ctx.restore();
  ctx.fillStyle='#9ecbff'; ctx.font='9px sans-serif'; ctx.fillText('zoom 0–'+xz.toFixed(0)+' ms', x0+4, y0+10);
  ctx.fillStyle='#8893a5'; ctx.fillText(hi.toFixed(2), x0+iw-26, y0+10); ctx.fillText(lo.toFixed(2), x0+iw-26, y0+ih-4);
}}
// Small fixed-size canvas for the per-axis evolution sparkline grid (cadre 3).
function mkMini(parent,w,h) {{ const c=document.createElement('canvas'); c.width=w; c.height=h;
  c.style.margin='2px 8px 6px 0'; c.style.display='inline-block'; parent.appendChild(c); return c; }}
// Hover a plotted point (stored in canvas._hpts as {{x,y,t}}) -> show its value in the shared #ptip.
function miniHover(canvas) {{
  canvas.onmousemove=(e)=>{{
    const r=canvas.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top, tip=document.getElementById('ptip');
    let best=null, bd=1e9;
    for (const pt of (canvas._hpts||[])) {{ const dd=(pt.x-mx)*(pt.x-mx)+(pt.y-my)*(pt.y-my); if (dd<bd) {{ bd=dd; best=pt; }} }}
    if (best && bd<169) {{ tip.textContent=best.t; tip.style.display='block'; tip.style.left=(e.clientX+12)+'px'; tip.style.top=(e.clientY+12)+'px'; }}
    else tip.style.display='none';
  }};
  canvas.onmouseleave=()=>{{ document.getElementById('ptip').style.display='none'; }};
}}
// One indicator's evolution across passes: median dot + min/max whisker (when a pass has it),
// a bare dot otherwise (single-sweep pass). Null medians (e.g. no crossover) break the line.
// opts.zones = [{{lo,hi,fill}}] horizontal reference bands; opts.ctx_lo/ctx_hi force the y-range
// to include a context value (so a reference band stays visible even when the data is far from it).
function miniRange(pts,opts) {{
  opts=opts||{{}}; let vals=[]; pts.forEach(p=>{{ if(p.v!=null)vals.push(p.v); if(p.lo!=null)vals.push(p.lo); if(p.hi!=null)vals.push(p.hi); }});
  if(opts.ctx_lo!=null)vals.push(opts.ctx_lo); if(opts.ctx_hi!=null)vals.push(opts.ctx_hi);
  if(!vals.length) return null;
  let ymin=Math.min(...vals), ymax=Math.max(...vals);
  if(ymax-ymin<1e-6) {{ ymax+=1; ymin-=1; }}
  const pad=(ymax-ymin)*0.14; return [ymin-pad, ymax+pad];
}}
function miniSeries(ctx,pts,xpos,ypos,color,dash) {{
  ctx.setLineDash(dash||[]); ctx.strokeStyle=color; ctx.globalAlpha=0.5; ctx.lineWidth=1;
  ctx.beginPath(); let started=false;
  pts.forEach((p,i)=>{{ if(p.v==null){{started=false;return;}} const x=xpos(i),y=ypos(p.v); started?ctx.lineTo(x,y):ctx.moveTo(x,y); started=true; }});
  ctx.stroke(); ctx.globalAlpha=1; ctx.setLineDash([]);
  pts.forEach((p,i)=>{{ const x=xpos(i);
    if(p.lo!=null&&p.hi!=null&&p.hi-p.lo>1e-9) {{ const y0=ypos(p.lo),y1=ypos(p.hi);
      ctx.strokeStyle=color; ctx.lineWidth=1.4; ctx.beginPath(); ctx.moveTo(x,y0); ctx.lineTo(x,y1);
      ctx.moveTo(x-3,y0); ctx.lineTo(x+3,y0); ctx.moveTo(x-3,y1); ctx.lineTo(x+3,y1); ctx.stroke(); }}
    if(p.v!=null) {{ ctx.fillStyle=color; ctx.beginPath(); ctx.arc(x,ypos(p.v),2.6,0,7); ctx.fill(); }} }});
}}
function drawMini(canvas,title,pts,color,opts) {{
  opts=opts||{{}};
  const ctx=canvas.getContext('2d'), cw=canvas.width, ch=canvas.height;
  const L=34, Rr=10, Tt=18, Bb=16, unit=opts.unit||'';
  ctx.clearRect(0,0,cw,ch); ctx.font='10px sans-serif';
  ctx.fillStyle=color; ctx.fillText(title,4,12);   // title in the indicator colour (shared identity)
  const rg=miniRange(pts,opts);
  if(!rg) {{ ctx.fillStyle='#5a6273'; ctx.fillText('—',L,ch/2); return; }}
  const [ymin,ymax]=rg, n=pts.length;
  const xpos=i=> n>1 ? L+(cw-L-Rr)*i/(n-1) : (L+cw-Rr)/2;
  const ypos=v=> (ch-Bb)-(v-ymin)/(ymax-ymin)*(ch-Bb-Tt);
  // reference zones (e.g. Ms healthy band) behind everything, clipped to the visible range
  for (const z of (opts.zones||[])) {{ const y1=ypos(Math.min(z.hi,ymax)), y0=ypos(Math.max(z.lo,ymin));
    if(y0>y1){{ ctx.fillStyle=z.fill; ctx.fillRect(L,y1,cw-Rr-L,y0-y1); }} }}
  ctx.strokeStyle='#2a2f3a'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(L,Tt); ctx.lineTo(L,ch-Bb); ctx.lineTo(cw-Rr,ch-Bb); ctx.stroke();
  ctx.fillStyle='#8893a5'; const dec=(ymax-ymin>=10)?0:1;
  ctx.fillText(ymax.toFixed(dec)+unit,2,Tt+7); ctx.fillText(ymin.toFixed(dec)+unit,2,ch-Bb+2);  // unit recalled on the ordinate
  miniSeries(ctx,pts,xpos,ypos,color,opts.dash);
  ctx.fillStyle='#8893a5'; pts.forEach((p,i)=>ctx.fillText(p.n, xpos(i)-3, ch-4));
  canvas._hpts=pts.map((p,i)=> p.v!=null ? {{x:xpos(i), y:ypos(p.v), t:p.v.toFixed(dec)+unit}} : null).filter(Boolean);
  miniHover(canvas);
}}
// Two indicators sharing one tile (independent left/right y-axes): A = left, solid; B = right, dashed.
// Title = the two labels in their own colour, each UNDERLINED with its line style (solid A / dashed B),
// so no "(plein)/(tireté)" words are needed. uA/uB are the units recalled on each ordinate.
function drawMini2(canvas,lA,lB,ptsA,ptsB,colA,colB,uA,uB) {{
  uA=uA||''; uB=uB||'';
  const ctx=canvas.getContext('2d'), cw=canvas.width, ch=canvas.height;
  const L=24, Rr=24, Tt=18, Bb=16;
  ctx.clearRect(0,0,cw,ch); ctx.font='10px sans-serif';
  // label A (solid underline) · label B (dashed underline)
  ctx.fillStyle=colA; ctx.fillText(lA,4,11); const wA=ctx.measureText(lA).width;
  ctx.strokeStyle=colA; ctx.lineWidth=1.4; ctx.beginPath(); ctx.moveTo(4,14); ctx.lineTo(4+wA,14); ctx.stroke();
  ctx.fillStyle='#8893a5'; ctx.fillText(' · ',4+wA,11); const wS=ctx.measureText(' · ').width, xB=4+wA+wS;
  ctx.fillStyle=colB; ctx.fillText(lB,xB,11); const wB=ctx.measureText(lB).width;
  ctx.strokeStyle=colB; ctx.setLineDash([3,2]); ctx.beginPath(); ctx.moveTo(xB,14); ctx.lineTo(xB+wB,14); ctx.stroke(); ctx.setLineDash([]);
  const ra=miniRange(ptsA), rb=miniRange(ptsB);
  if(!ra && !rb) {{ ctx.fillStyle='#5a6273'; ctx.fillText('—',L,ch/2); return; }}
  const n=ptsA.length;
  const xpos=i=> n>1 ? L+(cw-L-Rr)*i/(n-1) : (L+cw-Rr)/2;
  ctx.strokeStyle='#2a2f3a'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(L,Tt); ctx.lineTo(L,ch-Bb); ctx.lineTo(cw-Rr,ch-Bb); ctx.stroke();
  const hp=[];
  if(ra) {{ const [aMin,aMax]=ra, yA=v=>(ch-Bb)-(v-aMin)/(aMax-aMin)*(ch-Bb-Tt);
    ctx.fillStyle='#8893a5'; ctx.fillText(aMax.toFixed(0)+uA,0,Tt+7); ctx.fillText(aMin.toFixed(0)+uA,0,ch-Bb+2);
    miniSeries(ctx,ptsA,xpos,yA,colA);
    ptsA.forEach((p,i)=>{{ if(p.v!=null) hp.push({{x:xpos(i), y:yA(p.v), t:p.v.toFixed(0)+uA}}); }}); }}
  if(rb) {{ const [bMin,bMax]=rb, yB=v=>(ch-Bb)-(v-bMin)/(bMax-bMin)*(ch-Bb-Tt);
    ctx.fillStyle='#8893a5'; ctx.fillText(bMax.toFixed(0)+uB,cw-Rr+2,Tt+7); ctx.fillText(bMin.toFixed(0)+uB,cw-Rr+2,ch-Bb+2);
    miniSeries(ctx,ptsB,xpos,yB,colB,[3,2]);
    ptsB.forEach((p,i)=>{{ if(p.v!=null) hp.push({{x:xpos(i), y:yB(p.v), t:p.v.toFixed(0)+uB}}); }}); }}
  ctx.fillStyle='#8893a5'; ptsA.forEach((p,i)=>ctx.fillText(p.n, xpos(i)-3, ch-4));
  canvas._hpts=hp; miniHover(canvas);
}}
function hline(ctx,h,val,ymin,ymax,color,label) {{
  const y=lerp(val,ymin,ymax,h-22,8); ctx.strokeStyle=color; ctx.setLineDash([4,3]);
  ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-12,y); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle=color; ctx.fillText(label, W-70, y-3);
}}
function vline(ctx,h,f,fmin,fmax,color,label) {{
  if (!f || f<fmin || f>fmax) return;
  const x=logx(f,fmin,fmax); ctx.strokeStyle=color; ctx.lineWidth=1; ctx.setLineDash([2,3]);
  ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,h-22); ctx.stroke(); ctx.setLineDash([]);
  if (label) {{ ctx.fillStyle=color; ctx.fillText(label, x+2, 16); }}
}}
function vband(ctx,h,f0,f1,fmin,fmax,color) {{
  if (!f0||!f1) return; const a=logx(Math.max(f0,fmin),fmin,fmax), b=logx(Math.min(f1,fmax),fmin,fmax);
  if (b<=a) return; ctx.fillStyle=color; ctx.fillRect(a,8,b-a,h-30);
}}
function filterOverlay(ctx,h,fmin,fmax,fms) {{
  if (CFG.dyn_notch) vband(ctx,h,CFG.dyn_notch.min,CFG.dyn_notch.max,fmin,fmax,'rgba(255,212,121,0.07)');
  if (CFG.gyro_lpf1 && CFG.gyro_lpf1.dyn) {{ vline(ctx,h,CFG.gyro_lpf1.dyn[0],fmin,fmax,'#5a9bd4','gyroLPF'); vline(ctx,h,CFG.gyro_lpf1.dyn[1],fmin,fmax,'#5a9bd4',''); }}
  if (CFG.dterm_lpf1 && CFG.dterm_lpf1.dyn) {{ vline(ctx,h,CFG.dterm_lpf1.dyn[0],fmin,fmax,'#d48fd4','dtermLPF'); vline(ctx,h,CFG.dterm_lpf1.dyn[1],fmin,fmax,'#d48fd4',''); }}
  vline(ctx,h,fms,fmin,fmax,'#ffab40','f(Ms)');
}}
// Frequency where coherence drops below the gate for good (the trusted-band edge): scan for the
// first point past which it stays under GATE for a small window, so a single dip doesn't trip it.
function trustEdge(F,coh) {{
  if (!F || !F.length) return null;
  const n=F.length, win=Math.max(3,Math.floor(n*0.04));
  for (let i=0;i<n-win;i++) {{ let below=true;
    for (let j=i;j<i+win;j++) if (coh[j]>=GATE) {{ below=false; break; }}
    if (below) return F[i]; }}
  return F[n-1];
}}
// Shade the un-trusted (coherence < gate) region and mark the edge — echoed on coh, gain & phase so
// the eye sees the flat gain sits inside the trusted band.
function coherZone(ctx,h,ftrust,fmin,fmax,label) {{
  if (ftrust && ftrust<fmax) vband(ctx,h,ftrust,fmax,fmin,fmax,'rgba(126,138,160,0.11)');
  vline(ctx,h,ftrust,fmin,fmax,'#8a93a5',label||'');
}}
const root=document.getElementById('root');
const single = R.total_passes<=1;
const HIDDEN = new Set();   // pass indices whose overlay curves are hidden (pill toggles, global)

// --- Shared visual identity: one colour + pictogram per INDICATOR and per CONFIG item, reused
// everywhere they are named (tune score, evolution tiles, config tooltip, comparison table) so the
// eye links them at a glance. Filter colours match the Bode overlay (gyro/dterm/notch). ---
const IND={{
  overshoot:{{c:'#ff7a6b',p:'▲'}}, rise:{{c:'#ffc14d',p:'↑'}}, settle:{{c:'#59c2b0',p:'↓'}},
  margin:{{c:'#6fd36f',p:'∠'}}, ms:{{c:'#b58cff',p:'◎'}}, noise:{{c:'#4fa3e0',p:'≈'}}
}};
function citem(lbl) {{
  if (/P\\/I\\/D/.test(lbl)) return {{c:'#9ecbff',p:'⚙'}};
  if (/D_max/.test(lbl))    return {{c:'#ffab40',p:'▲'}};
  if (/gyro/i.test(lbl))    return {{c:'#5a9bd4',p:'∿'}};
  if (/D-term/i.test(lbl))  return {{c:'#d48fd4',p:'∿'}};
  if (/notch/i.test(lbl))   return {{c:'#ffd479',p:'▽'}};
  if (/RPM/i.test(lbl))     return {{c:'#aed581',p:'⟳'}};
  return {{c:'#9ad',p:'·'}};
}}
// A pass's config as coloured+pictogram HTML, for the rich hover tooltip on any pass label. Each
// field shows from→to (underlined) vs the previous pass, so a glance reveals exactly what moved.
function cfgHTML(p) {{
  const fields=cfgFields(p.config||{{}});
  let s='<b style="color:#cfe3ff">'+(LANG==='fr'?'Passe ':'Pass ')+p.n+'</b>'
    +(p.file?' <span style="color:#8893a5">'+p.file+'</span>':'');
  if (!fields.length) return s+'<div style="color:#8893a5">'+(LANG==='fr'?'(config non lue dans ce log)':'(no config parsed)')+'</div>';
  const idx=PASSES.indexOf(p);
  const prev=(idx>0)?Object.fromEntries(cfgFields(PASSES[idx-1].config||{{}})):null;
  if (prev) s+=' <span style="color:#8893a5">— Δ '+(LANG==='fr'?'vs passe ':'vs pass ')+PASSES[idx-1].n+'</span>';
  s+='<div style="margin-top:4px">';
  for (const [lbl,val] of fields) {{
    const ci=citem(lbl), changed=prev && prev[lbl]!=null && prev[lbl]!==val;
    const shown=changed ? ('<u style="color:#ffd479">'+prev[lbl]+' → '+val+'</u>') : val;
    s+='<div style="color:'+ci.c+'">'+ci.p+' '+lbl+' : <span style="color:#e6eaf2">'+shown+'</span></div>';
  }}
  return s+'</div>';
}}

// Teaching example for the throttle×freq map tooltip: a synthetic BAD map drawn with the SAME colour
// formula as the real one — a rising motor harmonic (freq grows with throttle), its 2nd harmonic, and a
// FIXED-frequency frame resonance, over a slightly raised floor; annotations baked in. Memoised data-URI.
let _mockuri=null;
function mockMapURI() {{
  if (_mockuri) return _mockuri;
  const NT=9, NF=64, W0=300, H0=130, cv=document.createElement('canvas'); cv.width=W0; cv.height=H0;
  const ctx=cv.getContext('2d'), ff=i=>20+480*i/(NF-1), g=(x,c,w)=>Math.exp(-0.5*((x-c)/w)**2);
  const M=[]; let lo=1e9, hi=-1e9;
  for (let r=0;r<NT;r++) {{ const t=0.15+0.85*r/(NT-1), row=[];
    for (let i=0;i<NF;i++) {{ const fr=ff(i);
      let v=-33 + 2*Math.sin(i*1.7+r);              // raised, mildly noisy floor
      v+=34*g(fr,110+320*t,17) + 18*g(fr,220+640*t,16) + 30*g(fr,230,11);
      row.push(v); lo=Math.min(lo,v); hi=Math.max(hi,v); }}
    M.push(row); }}
  const Lx=26, cw=(W0-Lx)/NF, chh=(H0-24)/NT;
  for (let r=0;r<NT;r++) for (let i=0;i<NF;i++) {{ const tn=(M[r][i]-lo)/((hi-lo)||1);
    ctx.fillStyle='rgb('+Math.round(255*Math.min(1,tn*1.6))+','+Math.round(120*Math.max(0,1-Math.abs(tn-0.5)*2))+','+Math.round(255*(1-tn))+')';
    ctx.fillRect(Lx+i*cw, 6+(NT-1-r)*chh, cw+1, chh+1); }}
  ctx.fillStyle='#c9d2e0'; ctx.font='8px sans-serif'; ctx.fillText('throttle ↑',1,12); ctx.fillText('freq →',W0-32,H0-2);
  ctx.fillStyle='#fff'; ctx.font='bold 9px sans-serif';
  ctx.fillText('↗ moteur', Lx+NF*cw*0.60, 20); ctx.fillText('│ résonance', Lx+2, H0-14);
  _mockuri=cv.toDataURL('image/png'); return _mockuri;
}}
function mapTipHTML() {{
  return '<b style="color:#cfe3ff">'+T('mapex_h')+'</b>'
    +'<img src="'+mockMapURI()+'" style="display:block;margin:6px 0;border-radius:4px;width:300px">'
    +'<div style="color:#c2cad6;max-width:300px;white-space:normal">'+T('mapex_cap')+'</div>';
}}

// Per-pass show/hide pills, repeated top-right of every axis block. They drive the global HIDDEN
// set, so toggling a pass here hides its overlaid curves across the whole report.
function passPills() {{
  if (single) return null;
  const wrap=el('div','passpills');
  PASSES.forEach((p,i)=>{{
    const off=HIDDEN.has(i), col=PAL[i%PAL.length];
    const b=document.createElement('button');
    b.className='pillbtn passtip'+(off?' off':''); b.textContent='P'+p.n;
    b.dataset.pass=i;
    b.style.borderColor=col; b.style.color=off?'#6b7689':col;
    b.onclick=()=>{{ off?HIDDEN.delete(i):HIDDEN.add(i); render(); }};
    wrap.appendChild(b);
  }});
  return wrap;
}}

function render() {{
  root.innerHTML='';
  W = Math.max(720, Math.min(1760, window.innerWidth - 48));   // responsive: fill the window
  document.getElementById('hdr').innerHTML =
      '<div class="banner-main"><span class="banner-icon">∿</span>'
    + '<div><div class="banner-title">'+T('title')+'</div>'
    + '<div class="banner-sub">'+T('subtitle')+'</div></div></div>'
    + '<div class="banner-tags"><span class="chip">Chirp</span><span class="chip">Analysis</span>'
    + '<span class="chip">Betaflight</span><span class="chip">Tuning</span></div>'
    + '<div class="banner-file">— '+FILE+'</div>';
  document.getElementById('langbtn').textContent = T('lang_btn');

  // ---- Guide ----
  {{
    const g=el('div','axis guide'); let s='<h2><span class=sicon>🧭</span>'+T('guide_h')+'</h2>';
    s+='<div class=pipe>'+T('pipe').split(' | ').map((x,i)=>'<b><span class=nidx>'+String(i+1).padStart(2,'0')+'</span>'+x+'</b>').join('<span class=arr>▸</span>')+'</div>';
    s+='<p>'+T('guide_order')
        .replace('{{filt}}',tip('filtering',T('w_filt')))
        .replace('{{pid}}',tip('pid',T('w_pid')))
        .replace('{{phase}}',tip('phase',T('w_phase')))
        .replace('{{pm}}',tip('phase_margin',T('w_pm')))
        .replace('{{res}}',tip('resonance',T('w_res')))+'</p>';
    s+='<p class=meta>'+T('guide_vsag')+'</p>';
    g.innerHTML=s; root.appendChild(g);
  }}

  // ---- TUNE score (composite 0-100 + delta vs previous pass: better/worse after a config change) ----
  if (PRI.tune_score && PRI.tune_score.overall!=null) {{
    const ts=PRI.tune_score;
    const box=el('div','axis score'); let s='<h2><span class=sicon>🎯</span>'+T('score_h')+'</h2>';
    let dtxt='';
    const prev=(PRIMARY>0 && PASSES[PRIMARY-1] && PASSES[PRIMARY-1].tune_score) ? PASSES[PRIMARY-1].tune_score : null;
    if (prev && prev.overall!=null) {{
      const dv=Math.round((ts.overall-prev.overall)*10)/10;
      const col=dv>0?'#7ddf7d':(dv<0?'#ff8a80':'#8893a5'), ar=dv>0?'▲':(dv<0?'▼':'=');
      dtxt='<span class=scoredelta style="color:'+col+'">'+ar+' '+(dv>0?'+':'')+dv+' '+T('score_vs')+'</span>';
    }}
    s+='<div class=scoreband><span class=scorebig>'+ts.overall.toFixed(0)+'<span class=scoremax>/100</span></span>'
     + '<span class=scoregrade>'+ts.grade+'</span>'+dtxt+'</div>';
    // Per-axis detail as a table: one column per indicator. Labels (header) carry the indicator's
    // colour + pictogram (same identity as the evolution tiles); values stay white, in their own cells.
    const SUBL={{overshoot:'overshoot', rise:T('sc_rise'), margin:T('sc_margin'), ms:'Ms', noise:T('sc_noise')}};
    const subKeys=Object.keys(SUBL).filter(k=>Object.keys(ts.axes).some(ax=>ts.axes[ax].subs[k]!=null));
    let head='<tr><th></th><th style="color:#9ecbff">'+T('score_h')+'</th>';
    for (const k of subKeys) head+='<th style="color:'+IND[k].c+'">'+IND[k].p+' '+SUBL[k]+'</th>';
    head+='</tr>';
    let rows='';
    for (const ax of Object.keys(ts.axes)) {{
      const a=ts.axes[ax];
      rows+='<tr><td><b>'+ax+'</b></td><td><b style="color:#9ecbff">'+a.score.toFixed(0)+'</b></td>';
      for (const k of subKeys) rows+='<td>'+(a.subs[k]!=null?a.subs[k]:'—')+'</td>';
      rows+='</tr>';
    }}
    s+='<table class=scoretab>'+head+rows+'</table>';
    // every pass's overall score (small), with a star on the best — the comparative view at a glance
    const scored=PASSES.map((p,i)=>({{n:p.n, i:i, v:(p.tune_score&&p.tune_score.overall)}})).filter(o=>o.v!=null);
    if (scored.length>1) {{
      const best=Math.max(...scored.map(o=>o.v));
      const line=scored.map(o=>'<span class="passtip" data-pass="'+o.i+'" style="color:'+PAL[o.i%PAL.length]+'">P'+o.n+' ('+o.v.toFixed(0)+')</span>'
        +(o.v===best?'<span style="color:#ffd479"> ★</span>':'')).join('  ·  ');
      s+='<div class="meta scoreall">'+T('score_all')+' '+line+'</div>';
    }}
    s+='<p class=meta>'+T('score_cap')+'</p>';
    box.innerHTML=s; root.appendChild(box);
  }}

  // ---- Per-axis indicator evolution (right after the score: it shows how each sub-metric moved
  // pass to pass, backing up the single number above) ----
  {{
    // One colour AND one pattern (solid) for every axis — the axes are told apart by their labelled
    // row, not by style. A second pattern (dashed) is used only inside the dual tile to separate its
    // two curves. Hover a point to read its value.
    const sm=(d,k)=>(d.step&&d.step.metrics)?d.step.metrics[k]:null;
    // Ms healthy/danger reference bands (cf. glossary): 1.3–2 = sain, >2 = nerveux/peu robuste.
    const MSZONES=[{{lo:1.3,hi:2.0,fill:'rgba(120,200,120,0.14)'}},{{lo:2.0,hi:9,fill:'rgba(255,120,120,0.12)'}}];
    // each tile carries the shared INDICATOR identity (colour+picto from IND), so a column links to
    // the same-coloured sub-score in the tune note above. Axes (rows) are told apart by their label.
    const INDIC=[
      {{k:'s', key:'overshoot', t:'overshoot', u:'%', g:d=>sm(d,'overshoot_pct'), r:d=>d.overshoot_range}},
      {{k:'s', key:'rise', t:(LANG==='fr'?'montée':'rise'), u:'ms', g:d=>sm(d,'rise_ms'), r:d=>d.rise_range}},
      {{k:'s', key:'settle', t:(LANG==='fr'?'établiss.':'settle'), u:'ms', g:d=>sm(d,'settle_ms'), r:d=>d.settle_range}},
      {{k:'d', uA:'°', uB:'Hz', gA:d=>d.pm_guaranteed_deg, rA:d=>d.pm_guaranteed_range, gB:d=>d.f_ms_hz, rB:d=>d.f_ms_range}},
      {{k:'s', key:'ms', t:'Ms', u:'', g:d=>d.ms, r:d=>d.ms_range, opts:{{ctx_lo:1.0, ctx_hi:2.1, zones:MSZONES}}}},
    ];
    const axesSet=[]; PASSES.forEach(p=>Object.keys(p.axes||{{}}).forEach(a=>{{ if(!axesSet.includes(a)) axesSet.push(a); }}));
    const ord=['roll','pitch','yaw']; axesSet.sort((a,b)=>ord.indexOf(a)-ord.indexOf(b));
    if (axesSet.length) {{
      const box=el('div','axis'); root.appendChild(box);
      box.appendChild(el('h2',null,'<span class=sicon>📈</span>'+T('evo_h')));
      box.appendChild(el('div','meta',T('evo_cap')));
      const mw=Math.max(170, Math.min(260, Math.floor((W-30)/3)-10)), mh=128;
      const ptsFor=(axis,g,r)=>PASSES.map(p=>{{ const d=(p.axes||{{}})[axis]; const v=d?g(d):null; const rg=d?r(d):null;
        return {{n:p.n, v:(v==null?null:v), lo:rg?rg[0]:null, hi:rg?rg[1]:null}}; }});
      for (const axis of axesSet) {{
        box.appendChild(el('div','passleg','<b style="border-bottom:2.5px solid #6b7689;padding-bottom:2px">'+axis.toUpperCase()+'</b>'));
        const grid=el('div'); grid.style.lineHeight='0'; box.appendChild(grid);
        for (const ind of INDIC) {{
          if (ind.k==='d') {{   // dual tile: margin (green, solid) + f(Ms) (purple, dashed) — indicator colours
            const A=ptsFor(axis,ind.gA,ind.rA), B=ptsFor(axis,ind.gB,ind.rB);
            if (A.some(p=>p.v!=null)||B.some(p=>p.v!=null)) drawMini2(mkMini(grid,mw,mh), (LANG==='fr'?'marge':'margin'), 'f(Ms)', A, B, IND.margin.c, IND.ms.c, ind.uA, ind.uB);
          }} else {{
            const col=IND[ind.key].c, pts=ptsFor(axis,ind.g,ind.r);
            const o2=Object.assign({{}}, ind.opts||{{}}, {{unit:ind.u||''}});
            if (pts.some(p=>p.v!=null)) drawMini(mkMini(grid,mw,mh), IND[ind.key].p+' '+ind.t, pts, col, o2);
          }}
        }}
      }}
    }}
  }}

  // (the former "current settings" cadre is dropped — the config is now in the pass-label tooltips
  //  and the settings-comparison table below.)

  // ---- Settings comparison (sits where the overview used to: the config diff across passes,
  //      changed cells highlighted; the per-metric evolution is already shown in the tiles above) ----
  if (!single) {{
    const ref=PASSES.map(p=>cfgFields(p.config||{{}})).filter(a=>a.length).slice(-1)[0]||[];
    if (ref.length) {{
      const box=el('div','axis step cmp'); root.appendChild(box);
      box.appendChild(el('h2',null,'<span class=sicon>🔀</span>'+T('cmp_h')));
      let changedAny=false, t='<table class=cmp><tr><th></th>';
      PASSES.forEach((p,i)=>{{ t+='<th><span class=swatch style="background:'+PAL[i%PAL.length]+'"></span><span class="passtip" data-pass="'+i+'">'+T('pass_word')+' '+p.n+'</span></th>'; }});
      t+='</tr>';
      for (const [lbl] of ref) {{
        const ci=citem(lbl);
        t+='<tr><td class=lbl><span style="color:'+ci.c+'">'+ci.p+'</span> '+lbl+'</td>'; let prev=null;
        PASSES.forEach(p=>{{ const m=Object.fromEntries(cfgFields(p.config||{{}})); const v=(lbl in m)?m[lbl]:'—';
          const chg=(prev!==null && v!==prev); if(chg)changedAny=true;
          t+='<td'+(chg?' class=chg':'')+'>'+v+'</td>'; prev=v; }});
        t+='</tr>';
      }}
      t+='</table>';
      if (!changedAny) t+='<p class=meta>'+T('cmp_none')+'</p>';
      box.appendChild(el('div',null,t));
    }}
  }}

  // ---- Sanity check: chirp sweep spectrogram (first — confirms the measurement actually swept the
  //      whole band before any tuning read; its own cadre, ahead of Filtering) ----
  {{
    const sg=PRI.spectrogram;
    if (sg && sg.levels_db && sg.levels_db.length) {{
      const box=el('div','axis'); root.appendChild(box);
      box.appendChild(el('h2',null,'<span class=sicon>🔍</span>'+tip('spectrogram',T('sanity_h'))+' <span class=meta>('+sg.axis+' gyro)</span>'));
      const rows=sg.levels_db.length, cols=sg.levels_db[0].length;
      const cw=W-PAD-12, Hs=Math.max(220,rows*1.6), cellW=cw/cols, cellH=(Hs-30)/rows;
      const ctx=mkCanvas(box,Hs).getContext('2d'); ctx.clearRect(0,0,W,Hs);
      const lo=-28, hi=0;   // fixed window for contrast: cells within 28 dB of each column's max
      for (let r=0;r<rows;r++) for (let c=0;c<cols;c++) {{
        const v=sg.levels_db[r][c]; const tn=Math.max(0,Math.min(1,(v-lo)/((hi-lo)||1)));
        ctx.fillStyle='rgb('+Math.round(255*Math.min(1,tn*1.6))+','+Math.round(150*Math.max(0,1-Math.abs(tn-0.55)*2))+','+Math.round(255*(1-tn))+')';
        ctx.fillRect(PAD+c*cellW, 8+(rows-1-r)*cellH, cellW+1, cellH+1);
      }}
      ctx.fillStyle='#8893a5'; ctx.font='10px sans-serif';
      // log frequency axis: decade ticks (1/2/5) placed by log position
      const fmn=sg.freqs[0], fmx=sg.freqs[sg.freqs.length-1];
      const lyy=fv=>8+(1-(Math.log10(fv)-Math.log10(fmn))/(Math.log10(fmx)-Math.log10(fmn)))*(Hs-30);
      for (let d=Math.floor(Math.log10(fmn)); d<=Math.ceil(Math.log10(fmx)); d++) for (const mm of [1,2,5]) {{
        const fv=mm*Math.pow(10,d); if (fv<fmn||fv>fmx) continue;
        ctx.fillText(fv>=1000?(fv/1000)+'k':fv, 4, lyy(fv)+3); }}
      const tmaxS=sg.t_s[sg.t_s.length-1]-sg.t_s[0];
      for (let k=0;k<=5;k++) {{ const x=PAD+k/5*cw; ctx.fillText((tmaxS*k/5).toFixed(1)+(k===5?' s':''), x-6, Hs-6); }}
      ctx.fillStyle='#9ecbff'; ctx.fillText('freq (Hz) ↑   temps →', PAD, Hs-18);
      let scap=T('spectro_cap').replace('{{sg}}',tip('spectrogram','spectrogramme')).replace('{{ax}}',sg.axis);
      if (sg.n_sweeps) scap+=' '+(LANG==='fr'
        ? 'Médiane de '+sg.n_sweeps+' sweeps (alignés sur le temps relatif) — la crête est plus nette, le bruit inter-sweeps moyenné.'
        : 'Median of '+sg.n_sweeps+' sweeps (aligned on relative time) — sharper ridge, inter-sweep noise averaged out.');
      box.appendChild(el('div','legend',scap));
    }}
  }}

  // ---- Step 1: Filtering ----
  {{
    const box=el('div','axis step'); root.appendChild(box);
    box.appendChild(el('h2',null,'<span class=sicon>🧹</span>'+tip('filtering',T('step1_h'))));
    const tm=PRI.throttle_map;
    if (tm && tm.freqs && tm.freqs.length) {{
      box.appendChild(el('h3',null,tip('throttle_map',T('tmap_h'))+' ('+tm.axis+' gyro · '+(tm.source||'?')+')'
        +' <span class="maptip" title="">?</span>'));
      const rows=tm.levels_db.length, cols=tm.freqs.length;
      // Robust colour scale: anchor to the 10th–98th percentiles, not the absolute min/max. With raw
      // min/max a single quiet cell drags the floor down and the whole map saturates red even when the
      // noise is fairly uniform — a contrast artefact, not "noisy everywhere". Percentiles fix that:
      // a calm map reads blue/green, only genuine hot-spots (top ~2%) go red.
      const flat=tm.levels_db.flat().filter(v=>v!==null).sort((a,b)=>a-b);
      const lo=flat[Math.floor(flat.length*0.10)], hi=flat[Math.floor(flat.length*0.98)];
      const cw=W-PAD-12, chh=22, H2=rows*chh+30;
      const ctx=mkCanvas(box,H2).getContext('2d'); ctx.clearRect(0,0,W,H2);
      for (let r=0;r<rows;r++) for (let c=0;c<cols;c++) {{
        const v=tm.levels_db[r][c]; if (v===null) continue; const tn=Math.max(0,Math.min(1,(v-lo)/((hi-lo)||1)));
        ctx.fillStyle='rgb('+Math.round(255*Math.min(1,tn*1.6))+','+Math.round(120*Math.max(0,1-Math.abs(tn-0.5)*2))+','+Math.round(255*(1-tn))+')';
        ctx.fillRect(PAD+c*cw/cols, 8+(rows-1-r)*chh, cw/cols+1, chh);
      }}
      ctx.fillStyle='#8893a5'; ctx.font='10px sans-serif';
      for (let r=0;r<rows;r++) ctx.fillText(tm.throttle_bins[r], 4, 8+(rows-1-r)*chh+14);
      const fmin=tm.freqs[0], fmax=tm.freqs[cols-1];
      for (let d=Math.floor(Math.log10(fmin));d<=Math.ceil(Math.log10(fmax));d++) for (const m of [1,2,5]) {{
        const f=m*Math.pow(10,d); if (f<fmin||f>fmax) continue;
        const x=PAD+(Math.log10(f)-Math.log10(fmin))/(Math.log10(fmax)-Math.log10(fmin))*cw;
        ctx.fillText(f>=1000?(f/1000)+'k':f, x-6, H2-6); }}
      ctx.fillStyle='#9ecbff'; ctx.fillText('throttle ↑   freq (Hz) →', PAD, H2-18);
      const tmx=f=>PAD+(Math.log10(f)-Math.log10(fmin))/(Math.log10(fmax)-Math.log10(fmin))*cw;
      const tvl=(f,col,lab)=>{{ if(!f||f<fmin||f>fmax)return; const x=tmx(f);
        ctx.strokeStyle=col; ctx.setLineDash([3,3]); ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,H2-26); ctx.stroke(); ctx.setLineDash([]);
        if(lab){{ctx.fillStyle=col; ctx.fillText(lab,x+2,18);}} }};
      if (CFG.dyn_notch) {{ tvl(CFG.dyn_notch.min,'#ffd479','dyn_notch'); tvl(CFG.dyn_notch.max,'#ffd479',''); }}
      for (const su of (PRI.filter_suggestions||[])) tvl(su.freq_hz,'#ff8a80','rés');
      box.appendChild(el('div','howto','<span class=meta>'+T('tmap_lo')+'</span><span class=scalebar></span><span class=meta>'+T('tmap_hi')+'</span>'));
      box.appendChild(el('div','howto',T('tmap_howto')));
    }} else {{
      box.appendChild(el('p','meta',tip('throttle_map',T('tmap_h'))+' — '+T('tmap_none')));
    }}

    // noise spectrum (raw vs filtered PSD, dB) — drives the filtering decision
    const ns=PRI.noise_spectrum;
    if (ns && ns.freqs && ns.freqs.length) {{
      box.appendChild(el('h3',null,tip('noise_psd',T('noise_h'))+' ('+ns.axis+' gyro)'));
      const F=ns.freqs, fmin=Math.max(30,F[0]), fmax=F[F.length-1];
      // floor-relative axis: 0 = noise floor. Scale to the noise region (95th pct) so a stray
      // low-freq motion bump doesn't squash the plot.
      const sorted=ns.raw_db.slice().sort((a,b)=>a-b); const hiR=sorted[Math.floor(sorted.length*0.97)];
      let lo=Math.max(-25,Math.min(-6,...ns.filt_db)), hi=Math.max(12,Math.ceil(hiR/5)*5+3);
      const H3=180, nc=mkCanvas(box,H3).getContext('2d');
      drawAxes(nc,H3,fmin,fmax,lo,hi,'dB/plancher');
      if (CFG.dyn_notch) vband(nc,H3,CFG.dyn_notch.min,CFG.dyn_notch.max,fmin,fmax,'rgba(255,212,121,0.07)');
      nc.font='10px sans-serif';
      // motor-harmonic bands (from eRPM): where motor noise lives -> a peak in a band is motor noise
      const mh=ns.motor;
      if (mh && mh.bands) for (const b of mh.bands) {{
        vband(nc,H3,b.lo,b.hi,fmin,fmax,'rgba(255,138,80,0.12)');
        if (b.hi>fmin && b.lo<fmax) {{ nc.fillStyle='#ff9a6a'; nc.fillText(b.n+'×', logx(Math.max(b.lo,fmin),fmin,fmax)+1, H3-24); }}
      }}
      // vertical lines = each filter's cut-off frequency (the LPF starts attenuating above it)
      const vcut=(fc,col,lab,yl)=>{{ if(!fc||fc<fmin||fc>fmax)return; const x=logx(fc,fmin,fmax);
        nc.strokeStyle=col; nc.lineWidth=1; nc.setLineDash([3,3]); nc.beginPath(); nc.moveTo(x,8); nc.lineTo(x,H3-22); nc.stroke(); nc.setLineDash([]);
        if(lab){{ nc.fillStyle=col; nc.fillText(lab,Math.min(x+2,W-44),yl||16); }} }};
      if (CFG.gyro_lpf1 && CFG.gyro_lpf1.dyn) {{ vcut(CFG.gyro_lpf1.dyn[0],'#5a9bd4'); vcut(CFG.gyro_lpf1.dyn[1],'#5a9bd4','gLPF1',16); }}
      if (CFG.gyro_lpf2) vcut(CFG.gyro_lpf2.static,'#79c0ff','gLPF2',16);
      if (CFG.dterm_lpf1 && CFG.dterm_lpf1.dyn) vcut(CFG.dterm_lpf1.dyn[1],'#d48fd4','dLPF1',28);
      if (CFG.dterm_lpf2) vcut(CFG.dterm_lpf2.static,'#d48fd4','dLPF2',28);
      hline(nc,H3,0,lo,hi,'#7e8aa0','plancher');                              // 0 dB = noise floor
      hline(nc,H3,{RESIDUAL_OK_DB:g},lo,hi,'#ff8a80','+{RESIDUAL_OK_DB:g} dB');  // indicative residual-resonance guide
      const ones=F.map(_=>1);
      if (ns.has_unfilt) plotLine(nc,H3,F,ns.filt_db,ones,fmin,fmax,lo,hi,'#80cbc4',{{lw:1.6}});
      plotLine(nc,H3,F,ns.raw_db,ones,fmin,fmax,lo,hi,'#4fc3f7',{{lw:1.8}});
      nc.font='10px sans-serif'; let _lab=0;
      for (const pk of (ns.peaks||[])) {{ if (pk.freq_hz<fmin||pk.freq_hz>fmax) continue;
        const x=logx(pk.freq_hz,fmin,fmax), y=lerp(pk.above_floor_db,lo,hi,H3-22,8);
        nc.fillStyle='#ffd479'; nc.beginPath(); nc.arc(x,y,2.6,0,7); nc.fill();
        if (pk.above_floor_db >= {RESIDUAL_OK_DB:g}) {{ const dy=(_lab++ %2)?12:-3;  // stagger to avoid overlap
          nc.fillText(pk.freq_hz.toFixed(0)+'Hz +'+pk.above_floor_db.toFixed(0)+'dB', x+4, y+dy); }} }}
      box.appendChild(el('div','legend',
        (ns.has_unfilt?('<span style="color:#4fc3f7">— '+T('leg_raw')+'</span><span style="color:#80cbc4">— '+T('leg_filt')+'</span>'):'<span style="color:#4fc3f7">— gyro</span>')+
        '<span style="color:#7e8aa0">-- '+T('leg_floor')+'</span>'+
        '<span style="color:#ff8a80">-- '+T('leg_resid')+'</span>'+
        '<span style="color:#5a9bd4">| '+tip('gyro_lpf','coupures gyro LPF')+'</span>'+
        '<span style="color:#d48fd4">| '+tip('dterm_lpf','coupures D-term LPF')+'</span>'+
        '<span style="color:#ffd479">▮ '+tip('dyn_notch','dyn_notch')+'</span>'+
        (ns.motor?'<span style="color:#ff9a6a">▮ '+tip('motor_harmonics',T('leg_motor'))+'</span>':'')));
      box.appendChild(el('div','legend',(ns.has_unfilt?T('noise_cap'):T('noise_cap_nounfilt')).replace('{{psd}}',tip('noise_psd','PSD'))));
    }}

    const fsug=PRI.filter_suggestions||[], nsug=PRI.noise_suggestions||[];
    let s='<details class="coll"><summary class="collh">'+tip('resonance',T('filt_h'))+'</summary><ul class="sugg filt">';
    for (const x of fsug) s+='<li>'+loc(x)+'</li>';
    for (const x of nsug) s+='<li>'+loc(x)+'</li>';
    if (!fsug.length && !nsug.length) s+='<li>—</li>';
    s+='</ul></details>'; box.appendChild(el('div',null,s));
  }}

  // ---- PID per axis (Bode + step response, all passes overlaid). No standalone section header:
  //      each axis block ("PID Roll/Pitch/Yaw") is its own cadre. ----
  for (const axis of Object.keys(PRI.axes||{{}})) {{
    const d=PRI.axes[axis]; if(!d||!d.freq) continue;
    const box=el('div','axis'); root.appendChild(box);
    const m=d.phase_margin_deg, fco=d.crossover_hz, mu=d.phase_margin_unc_deg;
    const ms=d.ms, fms=d.f_ms_hz, pmg=d.pm_guaranteed_deg;
    let mtxt;
    if (ms!=null) {{
      // Robust scalars only: Ms, f(Ms) and the guaranteed margin. The 0 dB crossover
      // ("bandwidth") and the measured margin are dropped here — on very damped axes the
      // crossover detection breaks down and reports nonsense (e.g. 2 Hz / 165°). The Bode
      // plots below still carry the full picture.
      mtxt = tip('sensitivity','Ms')+' '+ms.toFixed(2)+' @ '+(fms?fms.toFixed(0):'?')+' Hz'
           + ' · '+tip('phase_margin',T('pm_gtd'))+' ≥'+pmg.toFixed(0)+'°';
    }} else {{
      mtxt = m==null ? T('no_xover') : (tip('phase_margin',T('margin'))+' '+m.toFixed(0)+'°'+(mu?(' ±'+mu.toFixed(0)+'°'):'')+' @ '+(fco?fco.toFixed(0):'?')+' Hz');
    }}
    box.appendChild(el('h2',null,'<span class=sicon>🎛️</span>PID '+axis.charAt(0).toUpperCase()+axis.slice(1)+' <span class=meta>['+d.band_hz[0]+'–'+d.band_hz[1]+' Hz] — '+mtxt+'</span>'));
    if (!single) box.appendChild(el('div','meta',T('overlay')+' <i>('+T('overlay_hint')+')</i>'));
    const pills=passPills(); if (pills) box.appendChild(pills);
    const fmin=d.band_hz[0]||1, fmax=d.band_hz[1]||500;
    const ser=PASSES.map((p,i)=>({{p:p.axes&&p.axes[axis], i:i, primary:i===PRIMARY}})).filter(o=>o.p&&o.p.freq&&!HIDDEN.has(o.i));
    const PCOL=PAL[PRIMARY%PAL.length];   // primary pass colour, used for its inter-sweep band

    const wrap=v=>((v%360)+360)%360-360;
    // the trusted-band edge (coherence < gate), read on the primary pass and echoed on every plot
    const ftrust = trustEdge(d.freq, d.coherence);
    const trustLbl = (LANG==='fr'?'zone non fiable':'untrusted zone');

    // 1) Coherence first — it defines where the rest can be trusted; the 0.8 gate edge carries down.
    // The reliability note sits next to the title; the grey zone is labelled in-plot.
    box.appendChild(el('h3',null,tip('coherence',LANG==='fr'?'Cohérence':'Coherence')
      +' <span class="meta" style="text-transform:none;letter-spacing:0;font-weight:400">— '
      +T('coh_cap').replace('{{gate}}',GATE.toFixed(1))+'</span>'));
    let ch=mkCanvas(box,Hh-30).getContext('2d');
    drawAxes(ch,Hh-30,fmin,fmax,0,1,'coh');
    coherZone(ch,Hh-30,ftrust,fmin,fmax,trustLbl);
    hline(ch,Hh-30,GATE,0,1,'#7e8aa0',GATE.toFixed(1));
    if (d.coherence_band && !HIDDEN.has(PRIMARY)) plotBand(ch,Hh-30,d.freq,d.coherence_band[0],d.coherence_band[1],fmin,fmax,0,1,PCOL);
    for (const o of ser) plotLine(ch,Hh-30,o.p.freq,o.p.coherence,o.p.coherence.map(_=>1),fmin,fmax,0,1,PAL[o.i%PAL.length],{{dim:!o.primary, lw:o.primary?2:1.3}});

    // 2) Gain — filter-overlay legend moved up next to the title (the grey untrusted zone is still
    //    echoed from coherence on the plot, but no longer needs its own legend entry).
    const bodeLeg='<span style="text-transform:none;letter-spacing:0;font-weight:400;font-size:11px;margin-left:12px">'
      +'<span style="color:#5a9bd4;margin-right:12px">│ '+tip('gyro_lpf',T('leg_gyro'))+'</span>'
      +'<span style="color:#d48fd4;margin-right:12px">│ '+tip('dterm_lpf',T('leg_dterm'))+'</span>'
      +'<span style="color:#ffd479;margin-right:12px">▮ '+tip('dyn_notch',T('leg_notch'))+'</span>'
      +'<span style="color:#ffab40">│ '+tip('sensitivity',T('leg_fms'))+'</span></span>';
    box.appendChild(el('h3',null,tip('gain',T('bode_h'))+bodeLeg));
    let gAll=[]; ser.forEach(o=>gAll=gAll.concat(o.p.gain_db));
    if (d.gain_band) gAll=gAll.concat(d.gain_band[0],d.gain_band[1]);
    let gmin=Math.min(-12,...gAll), gmax=Math.max(12,...gAll);
    let g=mkCanvas(box,Hh).getContext('2d');
    drawAxes(g,Hh,fmin,fmax,gmin,gmax,'gain dB');
    coherZone(g,Hh,ftrust,fmin,fmax,'');
    filterOverlay(g,Hh,fmin,fmax,fms);
    hline(g,Hh,0,gmin,gmax,'#5a6273','0 dB');
    if (d.gain_band && !HIDDEN.has(PRIMARY)) plotBand(g,Hh,d.freq,d.gain_band[0],d.gain_band[1],fmin,fmax,gmin,gmax,PCOL);
    for (const o of ser) plotLine(g,Hh,o.p.freq,o.p.gain_db,o.p.coherence,fmin,fmax,gmin,gmax,PAL[o.i%PAL.length],{{dim:!o.primary, lw:o.primary?2.2:1.5}});

    // 3) Phase — same trusted-zone overlay.
    box.appendChild(el('h3',null,tip('phase',LANG==='fr'?'Phase':'Phase')));
    let p=mkCanvas(box,Hh).getContext('2d');
    drawAxes(p,Hh,fmin,fmax,-360,0,'phase °');
    coherZone(p,Hh,ftrust,fmin,fmax,'');
    hline(p,Hh,-180,-360,0,'#ff8a80','-180°');
    if (d.phase_band && !HIDDEN.has(PRIMARY)) plotBand(p,Hh,d.freq,d.phase_band[0].map(wrap),d.phase_band[1].map(wrap),fmin,fmax,-360,0,PCOL);
    for (const o of ser) plotLine(p,Hh,o.p.freq,o.p.phase_deg.map(wrap),o.p.coherence,fmin,fmax,-360,0,PAL[o.i%PAL.length],{{dim:!o.primary, lw:o.primary?2.2:1.5}});
    vline(p,Hh,fms,fmin,fmax,'#ffab40','f(Ms)');

    // step response (time domain)
    const sser=ser.filter(o=>o.p.step && o.p.step.t_ms && o.p.step.t_ms.length);
    if (sser.length) {{
      box.appendChild(el('h3',null,tip('step_response',T('step_h'))));
      // Full window on the main plot; y normalised to 0.25 steps so 1.0 is always a gridline.
      let xmax=0, ymax=1.0; sser.forEach(o=>{{ xmax=Math.max(xmax,o.p.step.t_ms[o.p.step.t_ms.length-1]); ymax=Math.max(ymax,...o.p.step.y); }});
      if (d.step.y_hi) ymax=Math.max(ymax,...d.step.y_hi);
      ymax=Math.ceil(ymax/0.25)*0.25;
      let st=mkCanvas(box,Hh).getContext('2d');
      drawAxesLin(st,Hh,xmax,0,ymax,'step',0.25,10);   // minor gridlines every 10 ms
      hline(st,Hh,1,0,ymax,'#5a6273','1.0');
      // rise time is measured 10% → 90% of the final value; show those two thresholds (labels left,
      // away from the lower-right inset) so the "rise X ms" number is self-explanatory.
      st.font='10px sans-serif';
      [[0.1,'10%'],[0.9,'90%']].forEach(([v,lb])=>{{ const y=lerp(v,0,ymax,Hh-22,8);
        st.strokeStyle='#3f4856'; st.setLineDash([2,3]); st.beginPath(); st.moveTo(PAD,y); st.lineTo(W-12,y); st.stroke(); st.setLineDash([]);
        st.fillStyle='#6b7689'; st.fillText(lb, PAD+3, y-2); }});
      if (d.step.y_lo && !HIDDEN.has(PRIMARY)) plotBandLin(st,Hh,d.step.t_ms,d.step.y_lo,d.step.y_hi,xmax,0,ymax,PCOL);
      for (const o of sser) plotLin(st,Hh,o.p.step.t_ms,o.p.step.y,xmax,0,ymax,PAL[o.i%PAL.length],{{dim:!o.primary, lw:o.primary?2.2:1.5}});
      stepInset(st,Hh,sser,d,PCOL);   // zoomed incrustation on the rise/overshoot (lower-right)
      const mt=d.step&&d.step.metrics;
      if (mt) box.appendChild(el('div','legend',T('metrics').replace('{{ov}}',mt.overshoot_pct).replace('{{rise}}',mt.rise_ms==null?'–':mt.rise_ms).replace('{{settle}}',mt.settle_ms==null?'–':mt.settle_ms)));
    }}
    // inter-sweep repeatability: median values are shown above; here is the measured min/max spread
    if (d.n_sweeps) {{
      const rg=a=>a&&a[0]!=null?('['+a[0]+'–'+a[1]+']'):'–';
      const fr='Répétabilité sur '+d.n_sweeps+' sweeps (bande ombrée = étendue min/max inter-sweeps) — overshoot '+rg(d.overshoot_range)+' %, montée '+rg(d.rise_range)+' ms, Ms '+rg(d.ms_range)+', marge '+rg(d.phase_margin_range)+'°.';
      const en='Repeatability over '+d.n_sweeps+' sweeps (shaded band = inter-sweep min/max range) — overshoot '+rg(d.overshoot_range)+' %, rise '+rg(d.rise_range)+' ms, Ms '+rg(d.ms_range)+', margin '+rg(d.phase_margin_range)+'°.';
      box.appendChild(el('div','legend',LANG==='fr'?fr:en));
    }}

    // (per-axis textual diagnosis intentionally omitted here — redundant with the evolution tiles
    // at the top; the observations remain in the text/JSON output for the LLM.)
  }}

  // ---- Glossary ----
  {{
    const order=['chirp','gain','phase','sensitivity','phase_margin','crossover','coherence','resonance',
      'noise_psd','motor_harmonics','filtering','gyro_lpf','dterm_lpf','dyn_notch','rpm_filter','dmax','pid','throttle_map','spectrogram','step_response','propwash'];
    const box=el('div','axis'); root.appendChild(box);
    let s='<details class="coll"><summary class="collh2"><span class=sicon>📖</span>'+T('glossary_h')+'</summary><dl class=glos>';
    // entries sorted alphanumerically by their displayed term name (in the active language)
    const entries=[];
    for (const k of order) {{ const g=GL[k]; if (g && (g[LANG]||g.fr)) {{
      const txt=(g[LANG]||g.fr); entries.push({{head:txt.split(/ : | — |: /)[0], txt:txt}}); }} }}
    entries.sort((a,b)=>a.head.localeCompare(b.head, LANG, {{numeric:true, sensitivity:'base'}}));
    for (const e of entries) s+='<dt>'+e.head+'</dt><dd>'+e.txt+'</dd>';
    s+='</dl></details>'; box.innerHTML=s;
  }}
}}
document.getElementById('langbtn').onclick=()=>{{ LANG = (LANG==='fr'?'en':'fr'); render(); }};
let _rt; window.addEventListener('resize', ()=>{{ clearTimeout(_rt); _rt=setTimeout(render, 150); }});
// Cursor-positioned HTML tooltip: pass config on a pass label (.passtip[data-pass]),
// or the good/bad throttle-map teaching example on the '?' badge (.maptip).
document.addEventListener('mousemove', e=>{{
  const ht=document.getElementById('htip');
  const pe=e.target.closest && e.target.closest('.passtip[data-pass]');
  const me=e.target.closest && e.target.closest('.maptip');
  if (pe) ht.innerHTML=cfgHTML(PASSES[+pe.dataset.pass]);
  else if (me) ht.innerHTML=mapTipHTML();
  else {{ ht.style.display='none'; return; }}
  ht.style.display='block';
  ht.style.left=Math.min(e.clientX+14, window.innerWidth-ht.offsetWidth-12)+'px';
  ht.style.top=Math.min(e.clientY+14, window.innerHeight-ht.offsetHeight-12)+'px';
}});
render();
</script></body></html>
"""


# ---------------------------------------------------------------------------
# Public entry: passes -> self-contained HTML (was the body of main()).
# ---------------------------------------------------------------------------
def build_report(passes: list, lang: str = "fr") -> str:
    """Assemble + render one or more analysis passes into a self-contained HTML report."""
    report = _assemble_report(passes, lang)
    primary_name = report["passes"][report["primary_index"]].get("file", "report")
    return _html_report(report, primary_name)
