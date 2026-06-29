"""Self-contained HTML report — multi-pass assembly + rendering.

Extracted verbatim from chirp_analysis.py (_assemble_report, _html_report,
GLOSSARY, STRINGS). Input: a list of pass dicts from analysis.chirp.build_pass.
"""
from __future__ import annotations

import json
import pathlib

from .config import config_fields, config_diff
from .analysis.chirp import COHERENCE_GATE, RESIDUAL_OK_DB

MAX_OVERLAY_PASSES = 8

# Per-axis fields the renderer needs from a NON-primary pass: the gyro raw/filtered PSD curves (for the
# per-pass overlay on the noise panel) and the D-term SNR scalar (for the evolution tile). Everything
# else (peaks, motor/dterm spectra, filter_quality) is primary-only and dropped to keep the HTML light.
_NOISE_SLIM_KEYS = ("axis", "has_unfilt", "freqs", "raw_db", "filt_db", "dterm_snr_db")


def _slim_noise(ns: dict) -> dict:
    """Keep only the per-axis curves + D-term SNR a non-primary pass is overlaid/tiled with."""
    axes = ns.get("axes") or {}
    slim = {}
    for a, d in axes.items():
        kept = {k: d[k] for k in _NOISE_SLIM_KEYS if k in d}
        if kept:
            slim[a] = kept
    return {"axes": slim} if slim else {}


def _assemble_report(passes: list, lang: str = "fr") -> dict:
    """Trim to the last MAX_OVERLAY_PASSES, attach pass numbers + config diffs, mark primary."""
    shown = passes[-MAX_OVERLAY_PASSES:]
    base = len(passes) - len(shown)
    primary = len(shown) - 1
    for k, p in enumerate(shown):
        p["n"] = base + k + 1
        p["ts"] = p.get("timestamp", "").replace("T", " ")
        # source .bbl name -> bare basename so the renderer can label/distinguish passes
        # (pill tooltip + comparison header). Absent file -> left untouched (mono-pass stays clean).
        if p.get("file"):
            p["file"] = pathlib.PurePath(p["file"]).name
        p["diff"] = config_diff(shown[k - 1]["config"], p["config"]) if k > 0 else ""
        # only the primary pass renders its heatmaps -> drop them from the others to keep the HTML light.
        # noise_spectrum is kept but SLIMMED on the others: the renderer overlays their gyro raw/filt
        # PSD on the noise panel (per-pass pills) and plots their D-term SNR in the evolution tile, so
        # the per-axis curves + the SNR scalar must survive — but the heavy peaks/motor/dterm/FQ blocks
        # (only ever shown for the primary) are dropped.
        if k != primary:
            for heavy in ("spectrogram", "throttle_map", "step_flight"):
                p.pop(heavy, None)
            if p.get("noise_spectrum"):
                p["noise_spectrum"] = _slim_noise(p["noise_spectrum"])
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
    "comp_sensitivity": {
        "fr": "Pic de sensibilité complémentaire Mt : Mt = max|T(f)|, où T = la boucle fermée "
              "mesurée par le chirp (gyro/consigne). Là où Ms = max|S| juge le rejet des perturbations "
              "et la robustesse aux erreurs de modèle au point le plus fragile, Mt est le pic de "
              "résonance de la boucle fermée elle-même. Repères : Mt ≈ 1.0–1.5 = bien amorti et "
              "robuste, notamment face aux retards purs (transport/calcul, déjà inclus dans le T "
              "mesuré) ; >1.5 = boucle peaky, peu amortie. Sert d'arbitre quand deux configs ont le "
              "même Ms : à Ms égal, le Mt le plus bas a le meilleur amortissement global et la "
              "meilleure robustesse aux délais. Mesuré sur la même bande cohérente que Ms, donc "
              "directement comparable.",
        "en": "Complementary sensitivity peak Mt: Mt = max|T(f)|, where T = the closed loop the chirp "
              "measures (gyro/setpoint). Where Ms = max|S| judges disturbance rejection and "
              "model-error robustness at the loop's most fragile point, Mt is the resonant peak of the "
              "closed loop itself. Rules of thumb: Mt ≈ 1.0–1.5 = well-damped and robust, notably to "
              "pure delay (transport/compute, already folded into the measured T); >1.5 = peaky, "
              "lightly-damped loop. Use it as the tie-break when two configs share the same Ms: at "
              "equal Ms, the lower Mt has the better global damping and delay robustness. Measured over "
              "the same coherent band as Ms, so directly comparable.",
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
    "filter_quality": {
        "fr": "Qualité du filtrage — deux angles complémentaires. "
              "Atténuation (A) : de la part de bruit qui émerge en pics au-dessus du plancher, combien le filtre en "
              "supprime ; A bas = bruit qui survit (sous-filtré, risque chauffe ESC). Préservation (P) : le coût de "
              "phase du filtre dans la bande de contrôle (retard de groupe ajouté) ; P bas = trop de filtre, délai "
              "excessif (sur-filtré). Le Score est leur moyenne harmonique 2AP/(A+P). A ou P peut être « n/a » "
              "quand rien n'émerge ou que le coût de phase n'est pas mesurable. "
              "1.0 = parfait · ≥ 0.8 = bon · 0.6–0.8 = acceptable · < 0.6 = à reconfigurer.",
        "en": "Filter quality — two complementary angles. "
              "Attenuation (A): of the noise that emerges as peaks above the floor, how much the filter removes; "
              "low A = noise survives (under-filtered, ESC-heat risk). Preservation (P): the filter's phase cost in "
              "the control band (added group delay); low P = too much filter, excess delay (over-filtered). Score is "
              "their harmonic mean 2AP/(A+P). A or P may read 'n/a' when nothing emerges or the phase cost isn't "
              "measurable. 1.0 = perfect · ≥ 0.8 = good · 0.6–0.8 = acceptable · < 0.6 = needs reconfiguring.",
    },
    "filter_delay": {
        "fr": "Budget de délai filtre : le retard de groupe ajouté par chaque filtre configuré (gyro LPF1/LPF2, "
              "D-term LPF1/LPF2, dynamic notch), calculé ANALYTIQUEMENT depuis la config — pas mesuré. Chaque "
              "filtre passe-bas retarde le signal (retard de groupe) ; ce retard, exprimé en ms, est directement "
              "comparable au délai de la réponse indicielle et se paie en marge de phase : plus on filtre bas, "
              "plus le retard monte et plus la marge fond. C'est le pendant prédictif de la jauge de qualité du "
              "filtrage (qui, elle, mesure l'atténuation réelle a posteriori) : le budget dit ce que la config "
              "DEVRAIT coûter, la jauge dit ce que le vol a réellement donné. La courbe « prédit » sur le spectre "
              "de bruit superpose cette même réponse analytique au raw→filtré mesuré, pour valider que le chain "
              "filtre se comporte comme configuré.",
        "en": "Filter delay budget: the group delay added by each configured filter (gyro LPF1/LPF2, D-term "
              "LPF1/LPF2, dynamic notch), computed ANALYTICALLY from the config — not measured. Every lowpass "
              "delays the signal (group delay); that delay, in ms, is directly comparable to the step-response "
              "delay and is paid for in phase margin: the lower the cutoff, the more delay and the less margin. "
              "It is the predictive companion to the filter-quality gauge (which measures the real attenuation "
              "after the fact): the budget says what the config SHOULD cost, the gauge says what the flight "
              "actually gave. The 'predicted' curve on the noise spectrum overlays this same analytic response "
              "onto the measured raw→filtered, to check the filter chain behaves as configured.",
    },
    "feedforward": {
        "fr": "Feedforward (FF) : un terme qui pousse la commande directement à partir du mouvement du "
              "manche (la dérivée de la consigne), sans attendre que le gyro voie l'erreur. Il accélère la "
              "réponse aux ordres rapides (flips, virages secs) sans toucher à la stabilité de la boucle PID — "
              "le chirp mesure la boucle fermée, donc le FF n'apparaît pas dans le Bode, mais il change le "
              "ressenti au manche. 0 = désactivé ; trop de FF = à-coups/overshoot sur les mouvements vifs.",
        "en": "Feedforward (FF): a term that drives the command straight from stick motion (the setpoint "
              "derivative), without waiting for the gyro to see the error. It speeds up the response to fast "
              "inputs (flips, hard turns) without touching PID-loop stability — the chirp measures the closed "
              "loop, so FF does not show up in the Bode plot, but it changes stick feel. 0 = off; too much FF = "
              "jerky/overshooting on quick moves.",
    },
    "dterm_psd": {
        "fr": "Spectre D-term / sortie moteur (PSD, dB) : densité de puissance du terme D (axisD) et de la "
              "commande moteur, hors chirp, référencée au plancher de bruit (0 dB = plancher). Le D est le chemin "
              "du PID qui domine la commande moteur en haute fréquence : un pic marqué en HF ici, c'est "
              "l'oscillation qui sature les ESC et chauffe les moteurs — le gyro filtré peut sembler propre alors "
              "que le D-term et les moteurs résonnent encore. La courbe moteur moyenne les PSD des moteurs (le "
              "bruit décorrélé s'atténue, une oscillation commune survit). À surveiller surtout au-dessus de "
              "~200 Hz : c'est là que vit la chauffe sans qu'on la sente au manche.",
        "en": "D-term / motor-output spectrum (PSD, dB): power density of the D term (axisD) and the motor "
              "command, outside the chirp, referenced to the noise floor (0 dB = floor). D is the PID path that "
              "dominates the motor command at high frequency: a sharp HF peak here is the oscillation that "
              "saturates the ESCs and heats the motors — the filtered gyro can look clean while the D-term and "
              "motors still ring. The motor curve averages the per-motor PSDs (uncorrelated noise averages down, a "
              "shared oscillation survives). Watch the band above ~200 Hz: that is where heat lives without being "
              "felt on the sticks.",
    },
    "step_flight": {
        "fr": "Step vol réel vs step chirp : deux réponses indicielles complémentaires. Le step CHIRP est "
              "reconstruit de la réponse en fréquence — c'est la réponse LINÉAIRE idéale de la boucle fermée. "
              "Le step VOL RÉEL est empilé depuis de vrais mouvements de manche (déconvolution consigne→gyro "
              "fenêtre par fenêtre), trié en deux paquets d'amplitude : PETIT step (petits ordres) et GROS step "
              "(ordres francs). L'intérêt : l'écart entre petit et gros révèle la NON-LINÉARITÉ que le step "
              "linéaire cache — saturation du feedforward, anti-gravity, iterm-relax. Si le gros step dépasse "
              "beaucoup plus que le petit, le FF/anti-gravity sature ; la bande grise = l'étendue réelle sur "
              "toutes les fenêtres (la dispersion d'un vol à l'autre), pas une incertitude théorique.",
        "en": "Real-flight step vs chirp step: two complementary step responses. The CHIRP step is reconstructed "
              "from the frequency response — the ideal LINEAR closed-loop response. The REAL-FLIGHT step is "
              "stacked from actual stick moves (per-window setpoint→gyro deconvolution), split into two amplitude "
              "bins: SMALL step (small commands) and LARGE step (sharp commands). The point: the gap between "
              "small and large reveals the NON-LINEARITY the linear step hides — feedforward saturation, "
              "anti-gravity, iterm-relax. If the large step overshoots much more than the small one, FF/"
              "anti-gravity is saturating; the grey band = the real spread across all windows (flight-to-flight "
              "scatter), not a theoretical uncertainty.",
    },
    "pid_balance": {
        "fr": "Équilibre P/I/D : la part de chaque terme PID dans la commande, mesurée par la RMS de axisP, "
              "axisI et axisD sur le vol. P domine normalement ; un I trop fort = rebond lent / wind-up ; un D "
              "élevé = amortissement mais aussi bruit/chaleur. Comparer les axes entre eux repère un axe "
              "déséquilibré (ex. yaw sans D, c'est normal). L'erreur de suivi (RMS de consigne−gyro, normalisée "
              "par la consigne) dit à quel point le drone SUIT réellement les ordres : basse = suivi serré. "
              "Indicateur de confirmation, pas un substitut au ressenti manche.",
        "en": "P/I/D balance: each PID term's share of the command, from the RMS of axisP, axisI and axisD over "
              "the flight. P normally dominates; too much I = slow bounce / wind-up; high D = damping but also "
              "noise/heat. Comparing axes flags an unbalanced one (e.g. yaw with no D is normal). The tracking "
              "error (RMS of setpoint−gyro, normalised by the setpoint) says how well the drone actually FOLLOWS "
              "commands: low = tight tracking. A confirmation indicator, not a substitute for stick feel.",
    },
    "dterm_snr": {
        "fr": "SNR D-term : rapport (dB) entre la puissance du signal D *utile* (réaction aux mouvements réels, "
              "< 100 Hz) et celle du bruit que la dérivation amplifie (> 100 Hz). Mesuré sur le gyro *non filtré* "
              "(gyroUnfilt) repondéré par f² (la dérivation multiplie l'amplitude par 2πf) : c'est donc le bruit "
              "que le filtre D *devrait* couper, pas ce qui reste après. Plus c'est haut, moins le chemin D est "
              "noyé dans le bruit → marge pour remonter ou désactiver le dterm_lpf2. Valeur pré-filtre, surtout "
              "comparée entre passes : reflète le bruit mécanique inhérent (moteurs/châssis), peu sensible au "
              "réglage de filtre courant.",
        "en": "D-term SNR: ratio (dB) of the *useful* D signal power (reaction to real motion, < 100 Hz) to the "
              "noise the derivative amplifies (> 100 Hz). Measured on the *unfiltered* gyro (gyroUnfilt) weighted "
              "by f² (differentiation multiplies amplitude by 2πf): it is the noise the D filter *should* cut, not "
              "what survives it. Higher = the D path is less noise-dominated → headroom to raise or disable "
              "dterm_lpf2. A pre-filter figure, most useful compared across passes: it reflects the inherent "
              "mechanical noise (motors/frame), largely insensitive to the current filter settings.",
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
        "lowcoh_warn": "⚠️ Cohérence trop basse{coh} — aucune excitation chirp détectée. La réponse en "
                       "fréquence (Bode), Ms, la marge de phase et la note de tune ne sont PAS fiables sur ce "
                       "log et sont masquées. Les blocs issus du vol réel (step vol, spectre de bruit, équilibre "
                       "P/I/D, budget de délai filtre) restent valides. Pour un Bode fiable, enregistre un log "
                       "chirp (debug_mode = CHIRP).",
        "pipe": "Blackbox | Identification fréquentielle | Réponse en fréquence | Phase margin / crossover | Step response simulée | Analyse bruit & filtrage | Scoring | Recommandations",
        "guide_order": "<b>Ordre recommandé :</b> on règle {filt} AVANT {pid}. Chaque filtre ajoute du retard "
                       "de {phase} qui grignote la {pm} : régler les gains avant d'avoir figé le filtrage donne "
                       "des PID qui ne tiendront plus ensuite. On nettoie donc le bruit et les {res} d'abord, "
                       "puis on monte les gains.",
        "cfg_init": "Réglages initiaux", "cfg_last": "Réglages — dernière passe", "cfg_sub": "(extraits du log)",
        "synth_h": "Lecture d'ensemble", "synth_intro": "D'après la dernière passe",
        "synth_evo": "Évolution depuis la passe 1",
        "score_h": "Note de tune", "score_vs": "vs passe précédente", "score_all": "Toutes les passes :",
        "score_click": "(clic = détailler cette passe)",
        "sc_rise": "montée", "sc_margin": "marge", "sc_noise": "bruit",
        "sc_track": "erreur",
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
        "noise_axes": "axes :", "noise_axis_other": "(filtré)",
        "dterm_h": "Spectre D-term / sortie moteur (PSD, dB)",
        "dterm_cap": "{psd} — D-term (axisD) par axe + sortie moteur moyennée, hors chirp. 0 dB = plancher. "
                     "C'est le chemin qui atteint les ESC : un pic en haute fréquence = oscillation qui sature et "
                     "chauffe les moteurs, même si le gyro filtré paraît propre.",
        "leg_dterm_sig": "D-term", "leg_motor_out": "sortie moteur",
        "snr_lbl": "SNR D-term :",
        "snr_hint": "(pré-filtre, split 100 Hz — plus haut = plus de marge pour remonter/désactiver dterm_lpf2)",
        "snr_tile": "SNR D",
        "ff_lbl": "FF", "ff_off": "FF désactivé",
        "ms_thr_t": "Ms / gaz", "mt_thr_t": "Mt / gaz",
        "fq_h": "Qualité du filtrage",
        "fq_atten": "Atténuation", "fq_pres": "Préservation", "fq_score": "Score global", "fq_mean": "moy.",
        "fq_cap": "Atténuation = bruit émergent supprimé · Préservation = coût de phase faible (peu de retard) · "
                  "Score = moyenne harmonique (pénalise le maillon faible) · vert ≥ 0.8 · ambre 0.6–0.8 · rouge < 0.6 · "
                  "▲ renforcer · ▼ alléger · ● équilibré",
        "fq_lag": "retard de phase",
        "fq_band_ctrl": "bande contrôle ≤",
        "fq_band_corner": "corner LPF",
        "fq_resid_warn": "pic résiduel au-dessus du plancher :",
        "fq_resid_ok": "aucun pic résiduel au-dessus du plancher",
        "fq_rec_decrease_strong": "Sur-filtré — alléger le filtrage (monter les cutoffs LPF) pour réduire le retard de phase",
        "fq_rec_decrease_slight": "Légèrement sur-filtré — alléger un peu le filtrage",
        "fq_rec_sweet_spot": "Filtrage équilibré",
        "fq_rec_increase_slight": "Légèrement sous-filtré — renforcer un peu le filtrage (bruit HF résiduel)",
        "fq_rec_increase_strong": "Sous-filtré — renforcer le filtrage (bruit HF résiduel, risque de chauffe moteur)",
        "fq_rec_insufficient_data": "Données insuffisantes (émergence de bruit non détectée)",
        "leg_pred": "prédit (config)",
        "fdl_h": "Budget de délai filtre",
        "fdl_gyro": "total gyro", "fdl_dterm": "total D-term",
        "fdl_cap": "Retard de groupe moyen sur 0–{ref} Hz par étage, déduit analytiquement de la config "
                   "(PT1/PT2/PT3, biquad, notch). Comparable au délai de la step et à la marge de phase : chaque "
                   "ms ajoutée ronge la marge. À lire avec la jauge de qualité (mesurée) au-dessus — l'une prédit "
                   "le coût de la config, l'autre mesure le résultat du vol.",
        "pidbal_h": "Équilibre P/I/D",
        "pidbal_cap": "RMS de chaque terme PID (axisP/I/D) sur le vol actif → quel terme domine la boucle et "
                      "l'équilibre inter-axes. err = RMS de l'erreur de suivi (consigne−gyro) ; normalisée par "
                      "la consigne, elle nourrit la note de tune (poids faible).",
        "stepf_h": "Réponse indicielle — vol réel (petit / gros step)",
        "stepf_cap": "Step empilé depuis le vol réel (déconvolution consigne→gyro par fenêtre ; bande = étendue "
                     "20–80e centile sur N fenêtres). Complète le step-chirp linéaire : l'écart petit↔gros step "
                     "révèle la non-linéarité (FF, anti-gravity, iterm-relax). Le gros step nourrit la note de "
                     "tune quand il est propre.",
        "stepf_small": "petit step", "stepf_large": "gros step",
        "stepf_none": "indisponible (pas assez de mouvements stick francs hors chirp)",
        "fq_rec_loosen_candidate": "Propre & faible retard — marge pour alléger encore le filtrage",
        "fq_rec_na_motion_dominated": "n/a — spectre dominé par le mouvement, filtrage HF non mesurable",
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
                   "visée, le rouge (>2) la zone nerveuse peu robuste. ★ = la meilleure passe pour cet indicateur.",
        "cmp_h": "Comparaison des réglages",
        "cmp_none": "Réglages PID + filtres identiques sur toutes les passes — les écarts de courbes "
                    "viennent du vol (batterie, throttle, bruit), pas du tune.",
        "glossary_h": "Glossaire",
        "w_filt": "le filtrage", "w_pid": "les PID", "w_phase": "phase",
        "w_pm": "marge de stabilité", "w_res": "résonances",
        "leg_gyro": "gyro lpf", "leg_dterm": "dterm lpf", "leg_notch": "plage dyn_notch",
        "leg_xover": "crossover 0 dB", "leg_fms": "f(Ms) — pic de sensibilité",
        "leg_fmt": "f(Mt) — pic compl.",
        "metrics": "overshoot {ov}% · montée {rise} ms · établi {settle} ms",
        "render_err": "⚠ Rendu interrompu : ",
    },
    "en": {
        "title": "CHIRP ANALYZER", "subtitle": "frequency-response analysis · Betaflight",
        "lang_btn": "FR", "pass_word": "Pass",
        "guide_h": "Tuning guide",
        "lowcoh_warn": "⚠️ Coherence too low{coh} — no chirp excitation detected. The frequency response "
                       "(Bode), Ms, phase margin and tune score are NOT reliable on this log and are hidden. "
                       "The real-flight blocks (flight step, noise spectrum, P/I/D balance, filter delay budget) "
                       "stay valid. For a reliable Bode, record a chirp log (debug_mode = CHIRP).",
        "pipe": "Blackbox | Frequency identification | Frequency response | Phase margin / crossover | Simulated step response | Noise & filtering analysis | Scoring | Recommendations",
        "guide_order": "<b>Recommended order:</b> set {filt} BEFORE {pid}. Every filter adds {phase} lag that "
                       "eats into the {pm}: tuning gains before the filtering is frozen gives PIDs that won't "
                       "hold afterwards. So clean up noise and {res} first, then raise the gains.",
        "cfg_init": "Initial settings", "cfg_last": "Settings — latest pass", "cfg_sub": "(read from the log)",
        "synth_h": "Overview", "synth_intro": "Based on the latest pass",
        "synth_evo": "Change since pass 1",
        "score_h": "Tune score", "score_vs": "vs previous pass", "score_all": "All passes:",
        "score_click": "(click to detail that pass)",
        "sc_rise": "rise", "sc_margin": "margin", "sc_noise": "noise",
        "sc_track": "error",
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
        "noise_axes": "axes:", "noise_axis_other": "(filtered)",
        "dterm_h": "D-term / motor-output spectrum (PSD, dB)",
        "dterm_cap": "{psd} — D-term (axisD) per axis + averaged motor output, outside the chirp. 0 dB = floor. "
                     "This is the path that reaches the ESCs: a high-frequency peak = an oscillation that "
                     "saturates and heats the motors, even when the filtered gyro looks clean.",
        "leg_dterm_sig": "D-term", "leg_motor_out": "motor output",
        "snr_lbl": "D-term SNR:",
        "snr_hint": "(pre-filter, 100 Hz split — higher = more headroom to raise/disable dterm_lpf2)",
        "snr_tile": "D SNR",
        "ff_lbl": "FF", "ff_off": "FF off",
        "ms_thr_t": "Ms / throttle", "mt_thr_t": "Mt / throttle",
        "fq_h": "Filter Quality",
        "fq_atten": "Attenuation", "fq_pres": "Preservation", "fq_score": "Global score", "fq_mean": "mean",
        "fq_cap": "Attenuation = emergent noise removed · Preservation = low phase cost (little delay) · "
                  "Score = harmonic mean (penalises weakest link) · green ≥ 0.8 · amber 0.6–0.8 · red < 0.6 · "
                  "▲ tighten · ▼ loosen · ● balanced",
        "fq_lag": "phase lag",
        "fq_band_ctrl": "control band ≤",
        "fq_band_corner": "LPF corner",
        "fq_resid_warn": "residual peak above the floor:",
        "fq_resid_ok": "no residual peak above the floor",
        "fq_rec_decrease_strong": "Over-filtered — loosen filtering (raise LPF cutoffs) to cut excess phase delay",
        "fq_rec_decrease_slight": "Slightly over-filtered — loosen filtering a little",
        "fq_rec_sweet_spot": "Filtering well balanced",
        "fq_rec_increase_slight": "Slightly under-filtered — tighten filtering a little (some HF noise survives)",
        "fq_rec_increase_strong": "Under-filtered — tighten filtering (HF noise survives — risk of motor heat)",
        "fq_rec_insufficient_data": "Insufficient data (no noise emergence detected)",
        "leg_pred": "predicted (config)",
        "fdl_h": "Filter delay budget",
        "fdl_gyro": "gyro total", "fdl_dterm": "D-term total",
        "fdl_cap": "Mean group delay over 0–{ref} Hz per stage, derived analytically from the config "
                   "(PT1/PT2/PT3, biquad, notch). Comparable to the step delay and the phase margin: every added "
                   "ms eats into the margin. Read alongside the measured quality gauge above — one predicts the "
                   "config cost, the other measures the flight result.",
        "pidbal_h": "P/I/D balance",
        "pidbal_cap": "RMS of each PID term (axisP/I/D) over the active flight → which term dominates the loop "
                      "and the inter-axis balance. err = tracking-error RMS (setpoint−gyro); normalised by the "
                      "setpoint, it feeds the tune score (low weight).",
        "stepf_h": "Step response — real flight (small / large step)",
        "stepf_cap": "Stacked step from real flight (per-window setpoint→gyro deconvolution; band = 20–80th "
                     "percentile spread over N windows). Complements the linear chirp step: the small↔large "
                     "gap reveals non-linearity (FF, anti-gravity, iterm-relax). The large step feeds the tune "
                     "score when it is clean.",
        "stepf_small": "small step", "stepf_large": "large step",
        "stepf_none": "unavailable (not enough sharp stick moves outside the chirp)",
        "fq_rec_loosen_candidate": "Clean & low-lag — room to loosen filtering further",
        "fq_rec_na_motion_dominated": "n/a — spectrum dominated by motion, HF filtering not measurable",
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
                   "low-robustness zone. ★ = the best pass for that indicator.",
        "cmp_h": "Settings comparison",
        "cmp_none": "Identical PID + filter settings across all passes — curve differences come from the "
                    "flight (battery, throttle, noise), not the tune.",
        "glossary_h": "Glossary",
        "w_filt": "filtering", "w_pid": "the PIDs", "w_phase": "phase",
        "w_pm": "stability margin", "w_res": "resonances",
        "leg_gyro": "gyro lpf", "leg_dterm": "dterm lpf", "leg_notch": "dyn_notch range",
        "leg_xover": "0 dB crossover", "leg_fms": "f(Ms) — sensitivity peak",
        "leg_fmt": "f(Mt) — compl. peak",
        "metrics": "overshoot {ov}% · rise {rise} ms · settle {settle} ms",
        "render_err": "⚠ Render interrupted: ",
    },
}


_ASSETS = pathlib.Path(__file__).parent / "report_assets"


def _asset(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


def _html_report(report: dict, file_name: str) -> str:
    """Render the assembled report dict into a self-contained HTML page by inlining
    the shared, mountable renderer (report_assets/chirp_report.{css,js}) — the SAME
    asset a web front can import, so the standalone report and a mounted front
    render from ONE source. The renderer is a global-exposing IIFE: we inline it in a
    classic <script>, then call mountChirpReport() into a host div."""
    css = _asset("chirp_report.css")
    js = _asset("chirp_report.js")
    payload = json.dumps(report).replace("</script", "<\\/script")
    head = (
        '<!DOCTYPE html>\n<html><head><meta charset="utf-8">\n'
        f"<title>Chirp report — {file_name}</title>\n"
        "<style>html,body{margin:0;background:#0d1016}\n" + css + "</style></head><body>\n"
    )
    boot = (
        "<script>mountChirpReport(document.getElementById('cr-host'), "
        + payload + ", {fileName: " + json.dumps(file_name) + "});</script>\n"
    )
    return head + '<div id="cr-host"></div>\n<script>' + js + "</script>\n" + boot + "</body></html>"


# ---------------------------------------------------------------------------
# Public entry: passes -> self-contained HTML (was the body of main()).
# ---------------------------------------------------------------------------
def build_report(passes: list, lang: str = "fr") -> str:
    """Assemble + render one or more analysis passes into a self-contained HTML report."""
    report = _assemble_report(passes, lang)
    primary_name = report["passes"][report["primary_index"]].get("file", "report")
    return _html_report(report, primary_name)


# Public alias — the assembled report dict, for callers that render it
# themselves (CLI text/JSON output) instead of taking the HTML.
assemble_report = _assemble_report
