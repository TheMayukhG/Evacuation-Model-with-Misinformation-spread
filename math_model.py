"""
math_model.py
-------------
PURE MATHEMATICAL FORMULAS FOR PEDESTRIAN EVACUATION & MISINFORMATION.
(The exact mathematical file to present to science judges and teachers).

Table of Equations:
1. Daley-Kendall / Kermack-McKendrick Rumor Epidemic Spread (SIR Model)
2. Helbing Social Force Collision Repulsion (Crowd Personal Space)
3. Wall Clearance Force (Corridor Centering)
4. Static Floor Field Gradient Descent (Downhill Navigation)
5. SFPE / NFPA Doorway Specific Discharge Capacity (Anti-Void Rate Limiting)
6. Physical Unit Scaling: Pixels to Real-World Meters & Speeds
"""

import math

# =====================================================================
# 1. PHYSICAL REAL-WORLD SCALE & VELOCITY METRICS
# =====================================================================
# Building Floor Plan: 1376 x 768 pixels representing a 90m x 50m campus wing
# Scale: 1 meter = 15.3 pixels (15.3 px/m)
# SFPE / Fruin Standard Human Evacuation Speeds:
#   - Normal walking speed: 1.25 m/s (~0.32 px/frame at 60 FPS)
#   - Panicked rush speed:  1.65 m/s (~0.42 px/frame at 60 FPS)
PIXELS_PER_METER = 15.3

def pixels_to_meters(pixels):
    """Converts screen distance in pixels to real-world meters."""
    return pixels / PIXELS_PER_METER

def velocity_to_mps(px_per_frame, fps=60):
    """Converts frame movement velocity to real-world meters per second (m/s)."""
    return (px_per_frame * fps) / PIXELS_PER_METER


# =====================================================================
# 2. EPIDEMIC RUMOR SPREAD (DALEY-KENDALL / SIR MODEL)
# =====================================================================
# In macroscopic epidemiology: dS/dt = -beta * S * (I / N)
# In microscopic agent interaction:
# Probability of infection decays linearly with distance up to hearing radius R:
#   P_infect = beta * (1 - d / R) * dt
def calculate_rumor_infection_probability(distance, hearing_radius=65.0, beta=1.6, dt=1.0/60.0):
    """
    Calculates the probability that an uninformed student hears and believes
    a panic rumor from a nearby spreader.
    """
    if distance > hearing_radius:
        return 0.0
    decay = 1.0 - (distance / hearing_radius)
    return beta * decay * dt


# =====================================================================
# 3. HELBING SOCIAL FORCE MODEL (COLLISION REPULSION & LATERAL BYPASS)
# =====================================================================
# Pedestrians preserve a personal space boundary (r_personal ~ 0.6m = 9.5px).
# When two pedestrians approach closer than r_personal, an inverse repulsive
# force pushes them apart:
#   F_collinear = ((r_personal - d) / d) * weight * normal_vector
# In counter-flow (opposing streams), humans instinctively take a slight
# lateral sidestep perpendicular to the collision vector (-dy, dx),
# breaking symmetry and forming natural counter-flow lanes without deadlocks:
#   F_repulse = F_collinear + F_lateral
def calculate_social_repulsion(dx, dy, distance, personal_space=9.5, force_weight=0.35, lateral_weight=0.35):
    """
    Calculates the 2D repulsive vector (push_x, push_y) between two agents.
    Includes both radial separation and tangential lateral sidestepping to
    prevent head-on deadlocks in narrow corridors (Helbing lane formation).
    """
    if distance <= 0.001 or distance >= personal_space:
        return 0.0, 0.0
    push_strength = ((personal_space - distance) / distance) * force_weight
    fx = (dx - lateral_weight * dy) * push_strength
    fy = (dy + lateral_weight * dx) * push_strength
    return fx, fy


# =====================================================================
# 4. WALL CLEARANCE FORCE (CORRIDOR CENTERING)
# =====================================================================
# Humans naturally avoid scraping shoulders against walls (~0.65m clearance).
# If an agent comes within d_limit of a wall, a repulsive force pushes them
# perpendicular to the wall into the open corridor:
#   F_wall = ((d_limit - d_wall) / d_limit) * strength * normal_wall
def calculate_wall_clearance(dist_to_wall, clearance_limit=10.0, strength=0.22):
    """
    Returns the magnitude of the push force away from a nearby wall.
    Keeps students centered in hallways instead of hugging walls.
    """
    if dist_to_wall >= clearance_limit:
        return 0.0
    return ((clearance_limit - dist_to_wall) / clearance_limit) * strength


# =====================================================================
# 5. STATIC FLOOR FIELD GRADIENT DESCENT (DIRECTION VECTOR)
# =====================================================================
# Each grid tile has a precomputed BFS walking distance D(x, y) to the door.
# The desired velocity vector points downhill along the negative gradient:
#   v_des = -nabla D(x, y) / ||nabla D(x, y)||
def normalize_direction_vector(dx, dy):
    """
    Normalizes a 2D direction vector to unit length (magnitude = 1.0).
    """
    length = math.hypot(dx, dy)
    if length > 0.0001:
        return dx / length, dy / length
    return 0.0, 0.0


# =====================================================================
# 6. DOORWAY SPECIFIC FLOW CAPACITY (NFPA / SFPE STANDARD)
# =====================================================================
# Standard fire engineering limits door discharge rate (Q_max ~ 1.5 to 2.5 peds/sec).
# Prevents people from vanishing into an instant void.
# For high-density building egress (360 students across 10 doors), capacity is 3-4 peds.
def can_enter_doorway(current_squeezing_count, max_capacity=3):
    """
    Returns True if the physical door frame has capacity to admit another person.
    """
    return current_squeezing_count < max_capacity


# =====================================================================
# 7. MULTI-STAGE COMPLEX CONTAGION & THRESHOLD MODEL
# =====================================================================
# In sociological crowd dynamics (Centola & Macy, 2007), panic rumors
# propagate through multi-exposure reinforcement thresholds:
#   Tier 0: Orderly (Unexposed, normal shortest-path routing)
#   Tier 1: Exposed (First rumor heard, redirects away from local door)
#   Tier 2: Reinforced (Second rumor confirmed, initiates distant wing detour)
#   Tier 3: Entrenched (Active panic carrier, transmits rumor to others)
MAX_INFECTION_TIER = 3


# =====================================================================
# 8. HELBING BOTTLENECK ARCHING & "FASTER-IS-SLOWER" EFFECT
# =====================================================================
# In disaster crowd dynamics (Helbing et al., Nature 2000), when panicked
# pedestrians rush a narrow bottleneck, transverse friction forces create
# physical arches (jamming transitions).
#
# When crowd queue size N at the doorway exceeds threshold N_cap:
#   1. Transit Delay increases exponentially with crowd pressure:
#        T_transit = T_base * (1.0 + gamma * (N - N_cap)^1.25)
#   2. Physical Capacity drops as bodies wedge into the opening:
#        Capacity drops from 3 to 1 ped/transit under severe compression.
#
# This reproduces the classic "Faster-is-Slower" phenomenon: panicked shoving
# forms dense heaps/clouts and delays total evacuation by 2.5x to 3x.
def calculate_doorway_arching_delay(queue_count, base_frames=50, threshold=3, max_multiplier=2.6):
    """
    Calculates extended transit delay caused by physical arching at a crowded doorway.
    As queue density builds beyond threshold, transverse friction at door jambs increases transit time,
    asymptotically saturating at max_multiplier to represent continuous crowd pressure.
    """
    if queue_count <= threshold:
        return base_frames
    overcrowd = queue_count - threshold
    jam_factor = 1.0 + (max_multiplier - 1.0) * (overcrowd / (overcrowd + 12.0))
    return int(base_frames * jam_factor)
