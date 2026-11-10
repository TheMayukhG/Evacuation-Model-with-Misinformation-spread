import sys, math, random, csv, numpy as np, pygame
import math_model

# Simulation & Window Constants
SCREEN_WIDTH, SCREEN_HEIGHT, CELL_SIZE, FPS = 1376, 768, 4, 60
NUM_STUDENTS, STUDENT_RADIUS = 360, 3.8
WALK_SPEED, PANIC_SPEED = 0.26, 0.36
MAX_DOOR_CAPACITY, DOOR_TRANSIT_FRAMES = 3, 50
MAX_INFECTION_TIER, SPATIAL_BIN_SIZE = 3, 36

# Colors
C_ORDERLY = (34, 197, 94)
C_TIERS = {1: (251, 146, 60), 2: (234, 88, 12), 3: (225, 29, 72)}
C_SEED, C_TALK, C_WALL = (239, 68, 68), (250, 204, 21), (235, 40, 40)


class StaticMap:
    def __init__(self, cache_file="map_cache.npz"):
        data = np.load(cache_file)
        self.grid, self.exit_id = data["grid"], data["exit_cell_id"]
        self.wall_push, self.flow_nearest = data["wall_push"], data["flow_nearest"]
        self.flow_exits, self.farthest_idx = data["flow_exits"], data["farthest_idx"]
        self.exit_names = list(data["exit_names"])
        self.gh, self.gw = self.grid.shape
        ys, xs = np.where((self.grid == 1) & ((self.flow_nearest[..., 0] != 0) | (self.flow_nearest[..., 1] != 0)))
        self.spawn_cells = list(zip(xs, ys))

    def is_walkable(self, x, y):
        gx, gy = int(x) // CELL_SIZE, int(y) // CELL_SIZE
        return 0 <= gx < self.gw and 0 <= gy < self.gh and self.grid[gy, gx] != 0

    def get_door_id(self, x, y):
        gx, gy = int(x) // CELL_SIZE, int(y) // CELL_SIZE
        return int(self.exit_id[gy, gx]) if 0 <= gx < self.gw and 0 <= gy < self.gh else -1


class Student:
    def __init__(self, s_id, x, y, state=0):
        self.id, self.x, self.y, self.state = s_id, float(x), float(y), state
        self.misinformed_count = 0
        self.target_exit = -1
        self.reaction_delay = random.uniform(2.5, 9.0)
        self.talk_timer = self.squeeze_timer = 0
        self.fade_timer, self.alpha = 70, 255
        self.escaped = False
        self.door_used = -1
        self.is_congested = False

    def get_color(self):
        if self.talk_timer > 0: return C_TALK
        if self.state == 1: return C_SEED
        return C_TIERS.get(min(self.misinformed_count, 3), C_ORDERLY)

    @property
    def is_carrier(self):
        return (self.state == 1 or self.misinformed_count >= 1) and not self.escaped

    def update(self, s_map, neighbors, sim_time):
        if self.escaped: return

        if self.squeeze_timer < 0:
            self.fade_timer -= 1
            self.alpha = max(0, int(255 * (self.fade_timer / 70)))
            if self.fade_timer <= 0: self.escaped = True
            return

        if self.squeeze_timer > 0:
            self.squeeze_timer -= 1
            if self.squeeze_timer == 0: self.squeeze_timer = -1
            return

        if sim_time < self.reaction_delay or self.talk_timer > 0:
            if self.talk_timer > 0: self.talk_timer -= 1
            return

        gx, gy = int(self.x) // CELL_SIZE, int(self.y) // CELL_SIZE
        if not (0 <= gx < s_map.gw and 0 <= gy < s_map.gh): return

        flow = s_map.flow_exits[self.target_exit, gy, gx] if self.target_exit >= 0 else s_map.flow_nearest[gy, gx]
        is_panicked = (self.state == 1 or self.misinformed_count >= 1)
        speed = PANIC_SPEED if is_panicked else WALK_SPEED
        des_vx, des_vy = flow[0] * speed, flow[1] * speed
        w_px, w_py = s_map.wall_push[gy, gx]

        p_space = 5.6 if is_panicked else 9.5
        lat_w = 0.05 if is_panicked else 0.35
        rep_x = rep_y = 0.0
        nearby_count = 0

        for other in neighbors:
            if other.id == self.id or other.escaped or other.squeeze_timer < 0: continue
            dx, dy = self.x - other.x, self.y - other.y
            dist = math.hypot(dx, dy)
            if dist < 14.0: nearby_count += 1
            fx, fy = math_model.calculate_social_repulsion(dx, dy, dist, personal_space=p_space, lateral_weight=lat_w)
            rep_x += fx
            rep_y += fy

        if is_panicked and nearby_count >= 2:
            rep_x *= 0.60
            rep_y *= 0.60

        nx, ny = self.x + des_vx + w_px + rep_x, self.y + des_vy + w_py + rep_y
        ox, oy = self.x, self.y

        if s_map.is_walkable(nx, ny): self.x, self.y = nx, ny
        elif s_map.is_walkable(nx, self.y): self.x = nx
        elif s_map.is_walkable(self.x, ny): self.y = ny

        self.is_congested = (math.hypot(self.x - ox, self.y - oy) < 0.14 and nearby_count >= 1)


class Simulation:
    def __init__(self, num_students=NUM_STUDENTS, misinformation_mode=False):
        self.num_students, self.misinformation_mode = num_students, misinformation_mode
        self.map = StaticMap("map_cache.npz")
        self.wall_congestion = np.zeros((self.map.gh, self.map.gw), dtype=int)
        self.door_centers = {}
        for d in range(len(self.map.exit_names)):
            ys, xs = np.where(self.map.exit_id == d)
            if len(xs) > 0:
                self.door_centers[d] = (int(np.mean(xs)) * CELL_SIZE + CELL_SIZE // 2, int(np.mean(ys)) * CELL_SIZE + CELL_SIZE // 2)
        self.reset()

    def reset(self):
        self.time_seconds = self.frame_count = 0
        self.is_finished = self.is_paused = self.has_logged_completion = False
        self.transmission_rays, self.jammed_doors, self.students, self.run_history = [], set(), [], []
        self.wall_congestion.fill(0)

        spawns = random.sample(self.map.spawn_cells, min(self.num_students, len(self.map.spawn_cells)))
        for i, (gx, gy) in enumerate(spawns):
            self.students.append(Student(i, gx * CELL_SIZE + CELL_SIZE // 2, gy * CELL_SIZE + CELL_SIZE // 2))

        if self.misinformation_mode:
            for s in random.sample(self.students, 5):
                s.state, s.misinformed_count = 1, MAX_INFECTION_TIER
                s.target_exit = int(self.map.farthest_idx[int(s.y) // CELL_SIZE, int(s.x) // CELL_SIZE])

    def add_misinformer_at(self, mx, my):
        c = min(self.students, key=lambda s: math.hypot(s.x - mx, s.y - my), default=None)
        if c and not c.escaped and math.hypot(c.x - mx, c.y - my) < 32.0:
            c.state, c.misinformed_count = 1, MAX_INFECTION_TIER
            c.target_exit = int(self.map.farthest_idx[int(c.y) // CELL_SIZE, int(c.x) // CELL_SIZE])

    def _get_bins(self):
        bins = {}
        for s in self.students:
            if not s.escaped and s.squeeze_timer == 0:
                bins.setdefault((int(s.x) // SPATIAL_BIN_SIZE, int(s.y) // SPATIAL_BIN_SIZE), []).append(s)
        return bins

    def step(self, is_fast_mode=False):
        if self.is_finished or self.is_paused: return
        self.frame_count += 1
        self.time_seconds += 1.0 / FPS
        self.transmission_rays = [(x1, y1, x2, y2, l - 1) for x1, y1, x2, y2, l in self.transmission_rays if l > 1] if not is_fast_mode else []

        bins = self._get_bins()

        if self.misinformation_mode:
            for c in [s for s in self.students if s.is_carrier and s.squeeze_timer == 0]:
                cbx, cby = int(c.x) // SPATIAL_BIN_SIZE, int(c.y) // SPATIAL_BIN_SIZE
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for o in bins.get((cbx + dx, cby + dy), []):
                            if o.misinformed_count < MAX_INFECTION_TIER and o.talk_timer == 0:
                                dist = math.hypot(o.x - c.x, o.y - c.y)
                                if random.random() < math_model.calculate_rumor_infection_probability(dist):
                                    c.talk_timer = o.talk_timer = 50
                                    o.misinformed_count += 1
                                    gx, gy = int(o.x) // CELL_SIZE, int(o.y) // CELL_SIZE
                                    if o.misinformed_count == 1:
                                        o.target_exit = int(self.map.farthest_idx[gy, gx])
                                    elif o.misinformed_count == 2:
                                        o.target_exit = (int(self.map.farthest_idx[gy, gx]) + 3) % len(self.map.exit_names)
                                    else:
                                        o.state = 2
                                    if not is_fast_mode:
                                        self.transmission_rays.append((c.x, c.y, o.x, o.y, 40))

        door_crowd = {}
        for d, (dcx, dcy) in self.door_centers.items():
            dbx, dby = dcx // SPATIAL_BIN_SIZE, dcy // SPATIAL_BIN_SIZE
            cnt = sum(1 for dx in (-1, 0, 1) for dy in (-1, 0, 1) for s in bins.get((dbx + dx, dby + dy), []) if math.hypot(s.x - dcx, s.y - dcy) < 38.0)
            door_crowd[d] = cnt
        self.jammed_doors = {d for d, cnt in door_crowd.items() if cnt > 4 and self.misinformation_mode}

        door_occ = {}
        for s in self.students:
            if s.squeeze_timer > 0: door_occ[s.door_used] = door_occ.get(s.door_used, 0) + 1

        for s in self.students:
            if s.squeeze_timer == 0 and not s.escaped:
                d_id = self.map.get_door_id(s.x, s.y)
                if d_id >= 0:
                    q_len = door_crowd.get(d_id, 0)
                    if self.misinformation_mode and q_len > 3:
                        delay = math_model.calculate_doorway_arching_delay(q_len, base_frames=DOOR_TRANSIT_FRAMES, threshold=3, max_multiplier=2.6)
                        cap = 1 if q_len > 10 else 2
                    else:
                        delay, cap = DOOR_TRANSIT_FRAMES, MAX_DOOR_CAPACITY

                    if math_model.can_enter_doorway(door_occ.get(d_id, 0), max_capacity=cap):
                        s.squeeze_timer, s.door_used = delay, d_id
                        door_occ[d_id] = door_occ.get(d_id, 0) + 1

        for s in self.students:
            if s.escaped: continue
            sbx, sby = int(s.x) // SPATIAL_BIN_SIZE, int(s.y) // SPATIAL_BIN_SIZE
            neighbors = [n for dx in (-1, 0, 1) for dy in (-1, 0, 1) for n in bins.get((sbx + dx, sby + dy), [])]
            s.update(self.map, neighbors, self.time_seconds)

            if s.is_congested and (not is_fast_mode or self.frame_count % 3 == 0):
                gx, gy = int(s.x) // CELL_SIZE, int(s.y) // CELL_SIZE
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        nx, ny = gx + dx, gy + dy
                        if 0 <= nx < self.map.gw and 0 <= ny < self.map.gh and self.map.grid[ny, nx] == 0:
                            self.wall_congestion[ny, nx] += 1

        escaped = sum(1 for s in self.students if s.escaped)
        if self.frame_count % 30 == 0 or escaped == self.num_students:
            self.run_history.append({
                "Time_Seconds": round(self.time_seconds, 2), "Evacuated": escaped,
                "Orderly": sum(1 for s in self.students if s.misinformed_count == 0 and not s.escaped),
                "Misinformed_Tier1": sum(1 for s in self.students if s.misinformed_count == 1 and not s.escaped),
                "Misinformed_Tier2": sum(1 for s in self.students if s.misinformed_count == 2 and not s.escaped),
                "Misinformed_Tier3": sum(1 for s in self.students if s.misinformed_count >= 3 and not s.escaped),
                "Misinformers": sum(1 for s in self.students if s.state == 1 and not s.escaped),
                "Congested": sum(1 for s in self.students if s.is_congested and not s.escaped),
                "Evacuation_Time_Seconds": round(self.time_seconds, 2)
            })

        if escaped == self.num_students and not self.is_finished:
            self.is_finished = True
            if not self.has_logged_completion:
                self.save_run_log("run_log.csv")
                self.has_logged_completion = True

    def save_run_log(self, filename="run_log.csv"):
        if not self.run_history: return
        with open(filename, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(self.run_history[0].keys()))
            writer.writeheader()
            writer.writerows(self.run_history)
        print(f"Evacuation metrics saved to '{filename}'.")

    def export_quick_chart(self, out_img="run_chart.png"):
        if not self.run_history: return
        try:
            import matplotlib.pyplot as plt
            ts = [r["Time_Seconds"] for r in self.run_history]
            plt.figure(figsize=(9, 5))
            plt.plot(ts, [r["Evacuated"] for r in self.run_history], color="#0284c7", lw=2.5, label="Evacuated")
            plt.plot(ts, [r["Orderly"] for r in self.run_history], color="#22c55e", lw=2, label="Orderly")
            if self.misinformation_mode:
                plt.plot(ts, [r["Misinformed_Tier1"] for r in self.run_history], "--", color="#fb923c", label="Tier 1")
                plt.plot(ts, [r["Misinformed_Tier2"] for r in self.run_history], "-.", color="#ea580c", label="Tier 2")
                plt.plot(ts, [r["Misinformed_Tier3"] for r in self.run_history], color="#e11d48", label="Tier 3")
            plt.title("Evacuation Progress", fontweight="bold")
            plt.xlabel("Seconds"); plt.ylabel("Students"); plt.legend(); plt.grid(True, linestyle=":", alpha=0.6)
            plt.tight_layout(); plt.savefig(out_img, dpi=180); plt.close()
            print(f"Chart saved to '{out_img}'.")
        except Exception as e:
            print(f"Chart export note: {e}")


def main():
    pygame.init()
    pygame.font.init()
    font = pygame.font.SysFont("Helvetica,Arial", 13)
    font_b = pygame.font.SysFont("Helvetica,Arial", 14, bold=True)
    pygame.display.set_caption("School Evacuation Simulator")

    try: screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SCALED)
    except Exception: screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))

    clock = pygame.time.Clock()
    bg = pygame.image.load("school_floorplan.bmp").convert()
    overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
    sim = Simulation()
    speed_map = {pygame.K_1: 1, pygame.K_2: 2, pygame.K_3: 5, pygame.K_4: 10, pygame.K_5: 20}
    sim_speed = 1

    def put_text(txt, x, y, col=(255, 255, 255), bold=False):
        screen.blit((font_b if bold else font).render(txt, True, col), (x, y))

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                running = False
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and event.pos[1] > 55:
                sim.add_misinformer_at(*event.pos)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_SPACE: sim.is_paused = not sim.is_paused
                elif event.key == pygame.K_r: sim.reset()
                elif event.key == pygame.K_m: sim.misinformation_mode = not sim.misinformation_mode; sim.reset()
                elif event.key == pygame.K_s: sim.save_run_log()
                elif event.key == pygame.K_p: sim.export_quick_chart()
                elif event.key in speed_map: sim_speed = speed_map[event.key]

        is_fast = (sim_speed >= 10)
        for _ in range(sim_speed):
            sim.step(is_fast_mode=is_fast)

        screen.blit(bg, (0, 0))

        # Congested walls
        ys, xs = np.where(sim.wall_congestion > 65)
        for gx, gy in zip(xs, ys):
            pygame.draw.rect(screen, C_WALL, (gx * CELL_SIZE, gy * CELL_SIZE, CELL_SIZE, CELL_SIZE))

        # Jammed doors
        for d in sim.jammed_doors:
            dcx, dcy = sim.door_centers[d]
            pygame.draw.circle(screen, (239, 68, 68), (dcx, dcy), 22, 2)
            put_text("ARCH JAM", dcx - 28, dcy - 24, (248, 113, 113), bold=True)

        # Transmission lines
        if not is_fast:
            for x1, y1, x2, y2, _ in sim.transmission_rays:
                pygame.draw.line(screen, C_TALK, (int(x1), int(y1)), (int(x2), int(y2)), 2)

        # Students
        overlay.fill((0, 0, 0, 0))
        for s in sim.students:
            if not s.escaped:
                pygame.draw.circle(overlay, (*s.get_color(), s.alpha), (int(s.x), int(s.y)), int(STUDENT_RADIUS))
                pygame.draw.circle(overlay, (20, 20, 20, s.alpha), (int(s.x), int(s.y)), int(STUDENT_RADIUS), 1)
        screen.blit(overlay, (0, 0))

        # HUD
        pygame.draw.rect(screen, (15, 23, 42), (0, 0, SCREEN_WIDTH, 52))
        pygame.draw.line(screen, (51, 65, 85), (0, 52), (SCREEN_WIDTH, 52), 1)
        m, s = int(sim.time_seconds) // 60, int(sim.time_seconds) % 60
        status = f"FINISHED in {m:02d}:{s:02d}s" if sim.is_finished else ("PAUSED" if sim.is_paused else f"{sim_speed}x SPEED")
        put_text(f"Elapsed: {m:02d}:{s:02d}s  {status}", 15, 8, (134, 239, 172) if sim.is_finished else (56, 189, 248), bold=True)
        put_text("MISINFORMATION OUTBREAK" if sim.misinformation_mode else "ORDERLY BASELINE", 15, 28, (249, 115, 22) if sim.misinformation_mode else C_ORDERLY)

        esc = sum(1 for st in sim.students if st.escaped)
        put_text(f"{esc}/{sim.num_students}", 450, 8, (56, 189, 248), bold=True)
        put_text(f"Orderly: {sum(1 for st in sim.students if st.misinformed_count == 0 and not st.escaped)}", 450, 28, C_ORDERLY)
        if sim.misinformation_mode:
            put_text(f"Tier 1: {sum(1 for st in sim.students if st.misinformed_count == 1 and not st.escaped)}", 545, 28, C_TIERS[1])
            put_text(f"Tier 2: {sum(1 for st in sim.students if st.misinformed_count == 2 and not st.escaped)}", 625, 28, C_TIERS[2])
            put_text(f"Carrier: {sum(1 for st in sim.students if (st.misinformed_count >= 3 or st.state == 1) and not st.escaped)}", 705, 28, C_TIERS[3])

        put_text("[M] Modes", SCREEN_WIDTH - 440, 8, (203, 213, 225))
        put_text("[SPACE] Pause | [R] Reset", SCREEN_WIDTH - 440, 28, (148, 163, 184))

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()

if __name__ == "__main__":
    main()