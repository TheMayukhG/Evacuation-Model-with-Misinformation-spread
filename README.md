# 🏫 School Evacuation & Multi-Stage Rumor Contagion Simulator
### (Presentation & Technical Demonstration Edition)

A high-performance pedestrian evacuation model designed to be demonstrated and explained in **2 minutes**.

---

## 📁 What Is in This Presentation Folder?

This folder contains **only 4 essential files** (zero experimental bloat):

```text
presentation/
├── math_model.py          <- [CODE 1] Pure equations to explain to judges (SIR, Social Force, SFPE)
├── main.py                <- [CODE 2] Visual simulation app + automated run recorder
├── map_cache.npz          <- [MAP 1]  Precomputed static flow fields & wall vectors (199 KB)
├── school_floorplan.bmp   <- [MAP 2]  High-resolution 90m x 50m campus floor plan
└── run_log.csv            <- [DATA]   Auto-generated time series record of the active evacuation
```

---

## 🚀 How to Run the Demonstration

```bash
cd presentation
python3 main.py
```

### 🎮 Controls & Demonstration Flow:

1. **High-Density Orderly Evacuation**:
   * Runs the baseline evacuation of **360 students** distributed across classrooms and corridors.
   * Evacuates evenly via 10 perimeter doors (~60–65s clearance).
   * Notice physical door rate limiting (NFPA standard: max 3 students simultaneously) and corridor centering.

2. **Multi-Stage Rumor Outbreak (`[M]`)**:
   * Introduces panic rumor contagion using a **multi-exposure reinforcement threshold**:
     * 🟢 **Tier 0 (Emerald)**: Uninformed, orderly shortest-path egress.
     * 🟠 **Tier 1 (Warm Amber)**: First exposure — doubts local door, detours toward opposite wing.
     * 🟧 **Tier 2 (Deep Coral)**: Second exposure — hears alternate rumor, redirects to a distant secondary door.
     * 🔴 **Tier 3 / Seed (Crimson)**: Entrenched panic — becomes an active carrier spreading rumors.
     * 🟡 **Conversation Pause (Yellow)**: Informer and receiver pause for ~1.0s to exchange rumors before turning around.

3. **Speed Controls (Including 20x Ultra Speed)**:
   * **`[1]`** : 1x Speed (Real-time 60 FPS)
   * **`[2]`** : 2x Speed
   * **`[3]`** : 5x Speed
   * **`[4]`** : 10x Speed
   * **`[5]`** : **20x Ultra Speed** (Runs the entire 360-student evacuation in **~5 seconds**!)

4. **Live Interaction & Data Export**:
   * **`[Left Click]`** : Click near any student to inject a rumor carrier live.
   * **`[S]`** : Manually export the time series log to `run_log.csv`.
   * **`[P]`** : Instantly generate and save an evacuation curve plot (`run_chart.png`).
   * **`[SPACE]`** : Pause / Resume.
   * **`[R]`** : Reset simulation with fresh randomized student positions.

---

## 📊 Recorded Run Data & Physical Calibration

The simulation rigorously reproduces the **Helbing "Faster-is-Slower" Clogging Phenomenon** (Nature 2000):

| Evacuation Scenario | Population | Dynamics Observed | Total Clearance Time | Delay Impact |
| :--- | :---: | :--- | :---: | :---: |
| **Orderly Baseline** | 360 | Laminar queues, polite personal spacing (9.5px), uniform 10-door dispersion | **1m 04s (64.1s)** | Baseline |
| **Misinformation Contagion** | 360 | Body compression (5.6px), dense crowd heaps, doorway arch jamming, 3-door bottleneck | **3m 04s (184.4s)** | **+187.7% (+120s / ~2.9x delay)** |

### Key Physical Mechanisms:
1. **Laminar Queues vs. Compressive Heaps**:
   * *Orderly*: Pedestrians respect personal buffer space ($d_{\text{space}} = 9.5\,\text{px}$) and exhibit polite lateral yielding ($\omega_{\text{lat}} = 0.35$), preventing crowd mounds and sustaining continuous door entry.
   * *Panicked*: Personal space compresses down to body contact ($d_{\text{space}} = 5.6\,\text{px}$), yielding drops to $0.05$, and forward shove pressure focuses pedestrians into semicircular **"clouts and heaps"** outside doorframes.
2. **Doorway Arching (Clogging Law)**:
   * When door queue size $N$ exceeds threshold ($N > 3$), transverse friction at door jambs increases door transit delay asymptotically by up to $2.6\times$, while effective door capacity pinches down to single-file passage ($C_{\text{eff}} = 1$).
   * Overcrowded doorways visually pulse with red **`ARCH JAM`** alert rings.

Whenever an evacuation finishes (or upon pressing `[S]`), the simulation saves a CSV record (`run_log.csv`):
* **`Time_Seconds`**: Simulation elapsed time.
* **`Evacuated`**: Cumulative count of escaped students.
* **`Orderly`**: Students following normal evacuation (Tier 0).
* **`Misinformed_Tier1`**: Initial detour count (Tier 1).
* **`Misinformed_Tier2`**: Secondary panic detour count (Tier 2).
* **`Misinformed_Tier3`**: Active entrenched carriers (Tier 3).
* **`Misinformers`**: Seed carriers.
* **`Congested`**: Students caught in crowd crush / bottlenecks.
* **`Evacuation_Time_Seconds`**: Official clearance timestamp.

This CSV can be opened directly in Excel, Google Sheets, or Python to plot comparison graphs.
