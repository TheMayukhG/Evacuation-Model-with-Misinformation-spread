import sys
import os
import math
import random
import csv
import numpy as np
import pygame
from PIL import Image, ImageDraw, ImageFont

# Pure mathematical models
import math_model

# =====================================================================
# CONSTANTS & CONFIGURATION
# =====================================================================
SCREEN_WIDTH = 1376
SCREEN_HEIGHT = 768
CELL_SIZE = 4
FPS = 60

# Population & Physical Speeds (SFPE / Fruin Standard)
NUM_STUDENTS = 360
STUDENT_RADIUS = 3.8
WALK_SPEED = 0.26   # ~1.00 m/s walking speed in crowds
PANIC_SPEED = 0.36  # ~1.40 m/s hurried evacuation speed

# Doorway Capacity (NFPA standard for double exit doors in large facilities)
MAX_DOOR_CAPACITY = 3
DOOR_TRANSIT_FRAMES = 50  # ~0.8s physical doorway clearance

# Contagion Thresholds
MAX_INFECTION_TIER = 3
SPATIAL_BIN_SIZE = 36     # Spatial partitioning grid size for O(N) performance

# Professional Color Hierarchy
COLOR_ORDERLY = (34, 197, 94)        # Emerald Green (Tier 0)
COLOR_TIER_1 = (251, 146, 60)        # Warm Amber (Tier 1: Initial Detour)
COLOR_TIER_2 = (234, 88, 12)         # Deep Coral (Tier 2: Secondary Panic Reroute)
COLOR_TIER_3 = (225, 29, 72)         # Crimson Red (Tier 3: Entrenched Carrier)
COLOR_SEED = (239, 68, 68)           # Ruby Red (Seed Misinformer)
COLOR_TALKING = (250, 204, 21)       # Vivid Yellow (Active conversation pause)
COLOR_WALL_HEAT = (235, 40, 40)      # High-congestion wall pressure

# =====================================================================
# TEXT RENDERING HELPER (Clean anti-aliased typography on macOS)
# =====================================================================
_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 13)
_font_bold = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
_font_cache = {}

def draw_text(surface, text, x, y, color=(255, 255, 255), bold=False):
    key = (text, color, bold)
    if key not in _font_cache:
        f = _font_bold if bold else _font
        w = int(f.getlength(text)) + 12
        img = Image.new("RGBA", (w, 22), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((0, 0), text, font=f, fill=color)
        _font_cache[key] = pygame.image.frombytes(img.tobytes(), img.size, "RGBA")
    surface.blit(_font_cache[key], (x, y))


# =====================================================================
# STATIC MAP LOADER (Precomputed vector fields in O(1) time)
# =====================================================================
class StaticMap:
    def __init__(self, cache_file="map_cache.npz"):
        data = np.load(cache_file)
        self.grid = data["grid"]                  # 0: Wall, 1: Floor, 2: Exit
        self.exit_id = data["exit_cell_id"]       # -1: None, 0..9: Door ID
        self.wall_push = data["wall_push"]        # (gh, gw, 2) corridor centering
        self.flow_nearest = data["flow_nearest"]  # (gh, gw, 2) normal evacuation
        self.flow_exits = data["flow_exits"]      # (10, gh, gw, 2) panic routes
        self.farthest_idx = data["farthest_idx"]  # (gh, gw) farthest exit index
        self.exit_names = list(data["exit_names"])
        self.gh, self.gw = self.grid.shape
        self.cell_size = CELL_SIZE
        
        # Valid interior spawn locations (classrooms and hallways, excluding sealed maintenance cells)
        ys, xs = np.where((self.grid == 1) & ((self.flow_nearest[:, :, 0] != 0) | (self.flow_nearest[:, :, 1] != 0)))
        self.spawn_cells = list(zip(xs, ys))

    def is_walkable(self, x, y):
        gx, gy = int(x) // self.cell_size, int(y) // self.cell_size
        if 0 <= gx < self.gw and 0 <= gy < self.gh:
            return self.grid[gy, gx] != 0
        return False

    def get_door_id(self, x, y):
        gx, gy = int(x) // self.cell_size, int(y) // self.cell_size
        if 0 <= gx < self.gw and 0 <= gy < self.gh:
            return int(self.exit_id[gy, gx])
        return -1


# =====================================================================
# STUDENT AGENT
# =====================================================================
class Student:
    def __init__(self, s_id, x, y, state=0):
        self.id = s_id
        self.x = float(x)
        self.y = float(y)
        self.state = state               # 0: Orderly, 1: Seed Carrier, 2: Misinformed
        self.misinformed_count = 0       # 0: Orderly, 1: Tier 1, 2: Tier 2, 3: Tier 3
        self.target_exit = -1            # -1: Nearest door; >= 0: Panicked door index
        self.reaction_delay = random.uniform(2.5, 9.0) # Pre-movement reaction delay
        self.talk_timer = 0              # Conversation pause frames
        self.squeeze_timer = 0           # Doorway transit frames
        self.fade_timer = 70             # Stairwell descent fade frames
        self.alpha = 255
        self.escaped = False
        self.door_used = -1
        self.is_congested = False

    def get_color(self):
        if self.talk_timer > 0:
            return COLOR_TALKING
        if self.state == 1:
            return COLOR_SEED
        if self.misinformed_count == 1:
            return COLOR_TIER_1
        if self.misinformed_count == 2:
            return COLOR_TIER_2
        if self.misinformed_count >= 3:
            return COLOR_TIER_3
        return COLOR_ORDERLY

    @property
    def is_carrier(self):
        return (self.state == 1 or self.misinformed_count >= 1) and not self.escaped

    def update(self, s_map, neighbor_students, sim_time):
        if self.escaped:
            return

        # Phase 2: Stairwell descent (smooth fade out)
        if self.squeeze_timer < 0:
            self.fade_timer -= 1
            self.alpha = max(0, int(255 * (self.fade_timer / 70)))
            if self.fade_timer <= 0:
                self.escaped = True
            return

        # Phase 1: Squeezing through narrow doorway
        if self.squeeze_timer > 0:
            self.squeeze_timer -= 1
            if self.squeeze_timer == 0:
                self.squeeze_timer = -1  # Enter stairwell fade phase
            return

        # Phase 0: Normal walking inside campus
        if sim_time < self.reaction_delay or self.talk_timer > 0:
            if self.talk_timer > 0:
                self.talk_timer -= 1
            return

        gx = int(self.x) // s_map.cell_size
        gy = int(self.y) // s_map.cell_size
        if not (0 <= gx < s_map.gw and 0 <= gy < s_map.gh):
            return

        # 1. Flow Direction (Nearest exit vs Panicked route)
        if self.target_exit >= 0:
            flow_dx, flow_dy = s_map.flow_exits[self.target_exit, gy, gx]
        else:
            flow_dx, flow_dy = s_map.flow_nearest[gy, gx]

        is_panicked = (self.state == 1 or self.misinformed_count >= 1)
        speed = PANIC_SPEED if is_panicked else WALK_SPEED
        des_vx = flow_dx * speed
        des_vy = flow_dy * speed

        # 2. Wall Clearance Force (centers agent in corridors)
        w_px, w_py = s_map.wall_push[gy, gx]

        # 3. Social Force Repulsion with Authentic Crowd Heaping
        # Orderly: polite spacing (9.5px) & polite yielding (0.35) -> forms neat queues
        # Panicked: compressed personal space (5.6px - body contact!) & aggressive forward drive (0.05 yielding) -> forms dense heaps/clouts!
        p_space = 5.6 if is_panicked else 9.5
        lat_w = 0.05 if is_panicked else 0.35

        rep_x, rep_y = 0.0, 0.0
        nearby_count = 0
        for other in neighbor_students:
            if other.id == self.id or other.escaped or other.squeeze_timer < 0:
                continue
            dx = self.x - other.x
            dy = self.y - other.y
            dist = math.hypot(dx, dy)
            if dist < 14.0:
                nearby_count += 1
            fx, fy = math_model.calculate_social_repulsion(dx, dy, dist, personal_space=p_space, lateral_weight=lat_w)
            rep_x += fx
            rep_y += fy

        # In panic heaps, forward desire relentlessly pushes through crowd towards the door
        if is_panicked and nearby_count >= 2:
            rep_x *= 0.60
            rep_y *= 0.60

        # 4. Total movement & wall sliding
        new_x = self.x + des_vx + w_px + rep_x
        new_y = self.y + des_vy + w_py + rep_y

        old_x, old_y = self.x, self.y
        if s_map.is_walkable(new_x, new_y):
            self.x, self.y = new_x, new_y
        elif s_map.is_walkable(new_x, self.y):
            self.x = new_x
        elif s_map.is_walkable(self.x, new_y):
            self.y = new_y

        actual_dist = math.hypot(self.x - old_x, self.y - old_y)
        self.is_congested = (actual_dist < 0.14 and nearby_count >= 1)


# =====================================================================
# SIMULATION ENGINE WITH SPATIAL PARTITIONING
# =====================================================================
class Simulation:
    def __init__(self, num_students=NUM_STUDENTS, misinformation_mode=False):
        self.num_students = num_students
        self.misinformation_mode = misinformation_mode
        self.map = StaticMap("map_cache.npz")
        self.wall_congestion = np.zeros((self.map.gh, self.map.gw), dtype=int)
        self.transmission_rays = []
        self.time_seconds = 0.0
        self.frame_count = 0
        self.is_finished = False
        self.is_paused = False
        self.students = []
        self.run_history = []
        self.has_logged_completion = False
        
        # Precompute door center coordinates for physical arching detection
        self.door_centers = {}
        for d in range(len(self.map.exit_names)):
            ys, xs = np.where(self.map.exit_id == d)
            if len(xs) > 0:
                self.door_centers[d] = (int(np.mean(xs)) * CELL_SIZE + CELL_SIZE // 2, int(np.mean(ys)) * CELL_SIZE + CELL_SIZE // 2)
        self.jammed_doors = set()
        self.reset()

    def reset(self):
        self.time_seconds = 0.0
        self.frame_count = 0
        self.is_finished = False
        self.has_logged_completion = False
        self.transmission_rays = []
        self.wall_congestion.fill(0)
        self.students = []
        self.run_history = []
        self.jammed_doors = set()

        # Spawn students randomly across classroom and hallway cells
        spawn_pool = random.sample(self.map.spawn_cells, min(self.num_students, len(self.map.spawn_cells)))
        for i, (gx, gy) in enumerate(spawn_pool):
            px = gx * CELL_SIZE + CELL_SIZE // 2
            py = gy * CELL_SIZE + CELL_SIZE // 2
            s = Student(i, px, py, state=0)
            self.students.append(s)

        # In rumor mode, seed 5 initial red misinformers
        if self.misinformation_mode:
            for s in random.sample(self.students, 5):
                s.state = 1
                s.misinformed_count = MAX_INFECTION_TIER
                gx, gy = int(s.x) // CELL_SIZE, int(s.y) // CELL_SIZE
                s.target_exit = int(self.map.farthest_idx[gy, gx])

    def add_misinformer_at(self, mx, my):
        """Click anywhere near a student to infect them into a rumor carrier live."""
        closest, min_d = None, 32.0
        for s in self.students:
            d = math.hypot(s.x - mx, s.y - my)
            if d < min_d:
                min_d = d
                closest = s
        if closest and not closest.escaped:
            closest.state = 1
            closest.misinformed_count = MAX_INFECTION_TIER
            gx, gy = int(closest.x) // CELL_SIZE, int(closest.y) // CELL_SIZE
            closest.target_exit = int(self.map.farthest_idx[gy, gx])

    def _build_spatial_bins(self):
        """Partitions active students into 2D spatial buckets for O(N) neighbor lookup."""
        bins = {}
        for s in self.students:
            if not s.escaped and s.squeeze_timer == 0:
                bx = int(s.x) // SPATIAL_BIN_SIZE
                by = int(s.y) // SPATIAL_BIN_SIZE
                bins.setdefault((bx, by), []).append(s)
        return bins

    def step(self, is_fast_mode=False):
        if self.is_finished or self.is_paused:
            return

        self.frame_count += 1
        self.time_seconds += 1.0 / FPS

        if not is_fast_mode:
            self.transmission_rays = [(x1, y1, x2, y2, life - 1) for x1, y1, x2, y2, life in self.transmission_rays if life > 1]
        else:
            self.transmission_rays.clear()

        # Build spatial acceleration structure
        bins = self._build_spatial_bins()

        # 1. Multi-Stage Rumor Contagion Spread
        if self.misinformation_mode:
            carriers = [s for s in self.students if s.is_carrier and s.squeeze_timer == 0]
            for c in carriers:
                cbx = int(c.x) // SPATIAL_BIN_SIZE
                cby = int(c.y) // SPATIAL_BIN_SIZE
                # Only check receptive students in neighboring bins
                for dbx in (-1, 0, 1):
                    for dby in (-1, 0, 1):
                        for o in bins.get((cbx + dbx, cby + dby), []):
                            if o.misinformed_count < MAX_INFECTION_TIER and o.talk_timer == 0:
                                dist = math.hypot(o.x - c.x, o.y - c.y)
                                prob = math_model.calculate_rumor_infection_probability(dist)
                                if random.random() < prob:
                                    # Both pause to converse
                                    c.talk_timer = 50
                                    o.talk_timer = 50
                                    o.misinformed_count += 1
                                    gx, gy = int(o.x) // CELL_SIZE, int(o.y) // CELL_SIZE

                                    # Multi-stage behavioral adaptation:
                                    if o.misinformed_count == 1:
                                        # Tier 1: Initial rumor -> detour to farthest opposite door
                                        o.target_exit = int(self.map.farthest_idx[gy, gx])
                                    elif o.misinformed_count == 2:
                                        # Tier 2: Secondary rumor -> panic reroute to an alternate distant door
                                        num_exits = len(self.map.exit_names)
                                        o.target_exit = (int(self.map.farthest_idx[gy, gx]) + 3) % num_exits
                                    else:
                                        # Tier 3: Entrenched conviction -> becomes active spreader
                                        o.state = 2

                                    if not is_fast_mode:
                                        self.transmission_rays.append((c.x, c.y, o.x, o.y, 40))

        # Measure crowd queue within 38px of each door center using spatial buckets
        door_crowd = {}
        for d, (dcx, dcy) in self.door_centers.items():
            dbx, dby = dcx // SPATIAL_BIN_SIZE, dcy // SPATIAL_BIN_SIZE
            cnt = 0
            for obx in (dbx-1, dbx, dbx+1):
                for oby in (dby-1, dby, dby+1):
                    for s in bins.get((obx, oby), []):
                        if math.hypot(s.x - dcx, s.y - dcy) < 38.0:
                            cnt += 1
            door_crowd[d] = cnt

        self.jammed_doors = {d for d, cnt in door_crowd.items() if cnt > 4 and self.misinformation_mode}

        # 2. Doorway Arching & Throughput Clogging (Helbing Bottleneck Physics)
        door_occupancy = {}
        for s in self.students:
            if s.squeeze_timer > 0:
                door_occupancy[s.door_used] = door_occupancy.get(s.door_used, 0) + 1

        for s in self.students:
            if s.squeeze_timer == 0 and not s.escaped:
                d_id = self.map.get_door_id(s.x, s.y)
                if d_id >= 0:
                    q_len = door_crowd.get(d_id, 0)
                    # In misinformation mode, crowding triggers physical arching and delays escape
                    if self.misinformation_mode and q_len > 3:
                        transit_delay = math_model.calculate_doorway_arching_delay(q_len, base_frames=DOOR_TRANSIT_FRAMES, threshold=3, max_multiplier=2.6)
                        effective_capacity = 1 if q_len > 10 else 2
                    else:
                        transit_delay = DOOR_TRANSIT_FRAMES
                        effective_capacity = MAX_DOOR_CAPACITY

                    if math_model.can_enter_doorway(door_occupancy.get(d_id, 0), max_capacity=effective_capacity):
                        s.squeeze_timer = transit_delay
                        s.door_used = d_id
                        door_occupancy[d_id] = door_occupancy.get(d_id, 0) + 1

        # 3. Update all agents using spatial neighbor queries
        for s in self.students:
            if s.escaped:
                continue
            sbx = int(s.x) // SPATIAL_BIN_SIZE
            sby = int(s.y) // SPATIAL_BIN_SIZE
            neighbors = []
            for dbx in (-1, 0, 1):
                for dby in (-1, 0, 1):
                    neighbors.extend(bins.get((sbx + dbx, sby + dby), []))

            s.update(self.map, neighbors, self.time_seconds)

            if s.is_congested and (not is_fast_mode or self.frame_count % 3 == 0):
                gx, gy = int(s.x) // CELL_SIZE, int(s.y) // CELL_SIZE
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        nx, ny = gx + dx, gy + dy
                        if 0 <= nx < self.map.gw and 0 <= ny < self.map.gh and self.map.grid[ny, nx] == 0:
                            self.wall_congestion[ny, nx] += 1

        # 4. Record Run History every 30 frames (0.5s) for charting
        escaped_total = sum(1 for s in self.students if s.escaped)
        if self.frame_count % 30 == 0 or escaped_total == self.num_students:
            self.run_history.append({
                "Time_Seconds": round(self.time_seconds, 2),
                "Evacuated": escaped_total,
                "Orderly": sum(1 for s in self.students if s.misinformed_count == 0 and not s.escaped),
                "Misinformed_Tier1": sum(1 for s in self.students if s.misinformed_count == 1 and not s.escaped),
                "Misinformed_Tier2": sum(1 for s in self.students if s.misinformed_count == 2 and not s.escaped),
                "Misinformed_Tier3": sum(1 for s in self.students if s.misinformed_count >= 3 and not s.escaped),
                "Misinformers": sum(1 for s in self.students if s.state == 1 and not s.escaped),
                "Congested": sum(1 for s in self.students if s.is_congested and not s.escaped),
                "Evacuation_Time_Seconds": round(self.time_seconds, 2)
            })

        # 5. Check evacuation completion
        if escaped_total == self.num_students and not self.is_finished:
            self.is_finished = True
            if not self.has_logged_completion:
                self.save_run_log("run_log.csv")
                self.has_logged_completion = True

    def save_run_log(self, filename="run_log.csv"):
        """Exports evacuation metrics to CSV."""
        if not self.run_history:
            return
        keys = list(self.run_history[0].keys())
        with open(filename, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(self.run_history)
        mins = int(self.time_seconds) // 60
        secs = int(self.time_seconds) % 60
        print(f"Evacuation metrics ({mins}m {secs:02d}s, 360 students) successfully saved to '{filename}'.")

    def export_quick_chart(self, out_img="run_chart.png"):
        """Renders an evacuation curve chart from the logged run."""
        if not self.run_history:
            return
        try:
            import matplotlib.pyplot as plt
            ts = [row["Time_Seconds"] for row in self.run_history]
            ev = [row["Evacuated"] for row in self.run_history]
            ord_c = [row["Orderly"] for row in self.run_history]
            t1 = [row["Misinformed_Tier1"] for row in self.run_history]
            t2 = [row["Misinformed_Tier2"] for row in self.run_history]
            t3 = [row["Misinformed_Tier3"] for row in self.run_history]

            plt.figure(figsize=(9, 5))
            plt.plot(ts, ev, color="#0284c7", linewidth=2.5, label="Evacuated Students")
            plt.plot(ts, ord_c, color="#22c55e", linewidth=2.0, label="Orderly (Tier 0)")
            if self.misinformation_mode:
                plt.plot(ts, t1, color="#fb923c", linewidth=1.8, linestyle="--", label="Tier 1: Initial Detour")
                plt.plot(ts, t2, color="#ea580c", linewidth=1.8, linestyle="-.", label="Tier 2: Secondary Reroute")
                plt.plot(ts, t3, color="#e11d48", linewidth=2.0, label="Tier 3: Entrenched Carrier")

            mode_lbl = "Multi-Stage Misinformation Contagion" if self.misinformation_mode else "Orderly Baseline"
            mins = int(self.time_seconds) // 60
            secs = int(self.time_seconds) % 60
            plt.title(f"Campus Evacuation Progress ({mode_lbl} | 360 Students | Clearance: {mins}m {secs:02d}s)", fontsize=11, fontweight="bold")
            plt.xlabel("Elapsed Time (seconds)", fontsize=10)
            plt.ylabel("Number of Students", fontsize=10)
            plt.legend(loc="center right", fontsize=8.5)
            plt.grid(True, linestyle=":", alpha=0.6)
            plt.tight_layout()
            plt.savefig(out_img, dpi=180)
            plt.close()
            print(f"Evacuation progress chart saved to '{out_img}'.")
        except Exception as e:
            print(f"Chart export note: {e}")


# =====================================================================
# MAIN APPLICATION LOOP
# =====================================================================
def main():
    pygame.init()
    pygame.display.set_caption("School Evacuation Simulator (360 Students | Multi-Stage Contagion)")

    try:
        screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SCALED)
    except Exception:
        screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))

    clock = pygame.time.Clock()
    bg_surface = pygame.image.load("school_floorplan.bmp").convert()
    overlay_surf = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)

    sim = Simulation(num_students=NUM_STUDENTS, misinformation_mode=False)
    sim_speed = 1  # 1x, 2x, 5x, 10x, 20x

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mx, my = pygame.mouse.get_pos()
                if my > 55:
                    sim.add_misinformer_at(mx, my)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_SPACE:
                    sim.is_paused = not sim.is_paused
                elif event.key == pygame.K_r:
                    sim.reset()
                elif event.key == pygame.K_m:
                    sim.misinformation_mode = not sim.misinformation_mode
                    sim.reset()
                elif event.key == pygame.K_s:
                    sim.save_run_log("run_log.csv")
                elif event.key == pygame.K_p:
                    sim.export_quick_chart("run_chart.png")
                elif event.key == pygame.K_1:
                    sim_speed = 1
                elif event.key == pygame.K_2:
                    sim_speed = 2
                elif event.key == pygame.K_3:
                    sim_speed = 5
                elif event.key == pygame.K_4:
                    sim_speed = 10
                elif event.key == pygame.K_5:
                    sim_speed = 20  # Ultra-Fast Mode (5-second complete evacuation)

        # Execute simulation steps for the active speed multiplier
        is_fast = (sim_speed >= 10)
        for _ in range(sim_speed):
            sim.step(is_fast_mode=is_fast)

        # 1. Render Background Floor Plan
        screen.blit(bg_surface, (0, 0))

        # 2. Render Congested Walls (Heatmap Cue)
        congested_ys, congested_xs = np.where(sim.wall_congestion > 65)
        for gx, gy in zip(congested_xs, congested_ys):
            pygame.draw.rect(screen, COLOR_WALL_HEAT, (gx * CELL_SIZE, gy * CELL_SIZE, CELL_SIZE, CELL_SIZE))

        # 3. Render Jammed Door Arching Rings (Helbing Bottlenecks)
        for d in sim.jammed_doors:
            dcx, dcy = sim.door_centers[d]
            pygame.draw.circle(screen, (239, 68, 68), (dcx, dcy), 22, 2)
            draw_text(screen, "ARCH JAM", dcx - 28, dcy - 24, (248, 113, 113), bold=True)

        # 4. Render Yellow Rumor Transmission Rays (only in normal/moderate speed)
        if not is_fast:
            for x1, y1, x2, y2, life in sim.transmission_rays:
                pygame.draw.line(screen, COLOR_TALKING, (int(x1), int(y1)), (int(x2), int(y2)), 2)

        # 5. Render Students with Multi-Tier Infection Hierarchy
        overlay_surf.fill((0, 0, 0, 0))
        for s in sim.students:
            if s.escaped:
                continue
            color = (*s.get_color(), s.alpha)
            pygame.draw.circle(overlay_surf, color, (int(s.x), int(s.y)), int(STUDENT_RADIUS))
            pygame.draw.circle(overlay_surf, (20, 20, 20, s.alpha), (int(s.x), int(s.y)), int(STUDENT_RADIUS), 1)
        screen.blit(overlay_surf, (0, 0))

        # 6. Render Professional Top Navigation Bar
        pygame.draw.rect(screen, (15, 23, 42), (0, 0, SCREEN_WIDTH, 52))
        pygame.draw.line(screen, (51, 65, 85), (0, 52), (SCREEN_WIDTH, 52), 1)

        # HUD Left: Timer & Execution State
        mins = int(sim.time_seconds) // 60
        secs = int(sim.time_seconds) % 60
        if sim.is_finished:
            status_txt = f"FINISHED in {mins:02d}:{secs:02d}s (Logged)"
            status_col = (134, 239, 172)
        elif sim.is_paused:
            status_txt = "PAUSED"
            status_col = (250, 204, 21)
        else:
            status_txt = f"{sim_speed}x {'ULTRA SPEED' if sim_speed == 20 else 'SPEED'}"
            status_col = (56, 189, 248) if sim_speed < 20 else (244, 63, 94)

        draw_text(screen, f"Elapsed: {mins:02d}:{secs:02d}s  {status_txt}", 15, 8, status_col, bold=True)

        mode_txt = "MISINFORMATION OUTBREAK" if sim.misinformation_mode else "ORDERLY BASELINE"
        flow_txt = f"Flow: ARCHING & HEAPS ({len(sim.jammed_doors)} Clogged Doors)" if sim.misinformation_mode else "Flow: LAMINAR QUEUES"
        mode_col = (249, 115, 22) if sim.misinformation_mode else (34, 197, 94)
        draw_text(screen, f"{mode_txt}", 15, 28, mode_col)

        # HUD Center: Detailed Population & Stage Metrics
        escaped_n = sum(1 for s in sim.students if s.escaped)
        orderly_n = sum(1 for s in sim.students if s.misinformed_count == 0 and not s.escaped)
        t1_n = sum(1 for s in sim.students if s.misinformed_count == 1 and not s.escaped)
        t2_n = sum(1 for s in sim.students if s.misinformed_count == 2 and not s.escaped)
        t3_n = sum(1 for s in sim.students if (s.misinformed_count >= 3 or s.state == 1) and not s.escaped)

        draw_text(screen, f"{escaped_n}/{sim.num_students}", 450, 8, (56, 189, 248), bold=True)
        draw_text(screen, f"Orderly: {orderly_n}", 450, 28, COLOR_ORDERLY)
        if sim.misinformation_mode:
            draw_text(screen, f"Tier 1: {t1_n}", 545, 28, COLOR_TIER_1)
            draw_text(screen, f"Tier 2: {t2_n}", 625, 28, COLOR_TIER_2)
            draw_text(screen, f"Carrier: {t3_n}", 705, 28, COLOR_TIER_3)

        # HUD Right: Clean Technical Hotkeys
        draw_text(screen, "[M] Modes", SCREEN_WIDTH - 440, 8, (203, 213, 225))
        draw_text(screen, "[SPACE] Pause | [R] Reset", SCREEN_WIDTH - 440, 28, (148, 163, 184))

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()

if __name__ == "__main__":
    main()
