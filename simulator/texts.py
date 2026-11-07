"""Complaint text templates (English and a little Hinglish) with optional typos."""

PLACES = ["bathroom", "kitchen", "balcony", "master bathroom", "wash area"]
ROOMS = ["bedroom", "living room", "kitchen", "hall", "study room"]

EN = {
    "plumbing": ["Water leaking from the {place} ceiling since {n} days",
                 "{place} tap is broken and water keeps running",
                 "Drain in {place} is blocked, water is not going down",
                 "Very low water pressure in the {place}"],
    "electrical": ["Power socket in the {room} is sparking",
                   "Lights in the {room} keep flickering",
                   "MCB trips again and again since {n} days",
                   "Fan in the {room} is not working"],
    "noise": ["Loud music from the flat above after 11 pm",
              "Construction noise from the neighbouring flat during the night",
              "Neighbours shouting late at night repeatedly"],
    "parking": ["Someone has parked in my allotted slot",
                "Visitor car is blocking the tower entrance",
                "Two wheelers parked in the passage again"],
    "lift": ["Lift in tower {tower} is stuck between floors",
             "Lift is making a loud noise and shaking",
             "Lift has not been working since morning"],
    "security": ["Guard was not at the gate during the night",
                 "Unknown person entered the tower without registration",
                 "CCTV camera near the entrance is not working"],
    "cleanliness": ["Garbage not collected from our floor for {n} days",
                    "Staircase is dirty and smells bad",
                    "Stray dogs and litter near the tower"],
    "other": ["Intercom is not working",
              "Water tank is overflowing on the terrace",
              "Need a new key for the common terrace door",
              "Gate pass not received for the new tenant"],
}
HI = {
    "plumbing": ["{place} me pani leak ho raha hai", "{place} ka nal toot gaya hai, plumber bhejo"],
    "electrical": ["{room} me light baar baar band ho rahi hai", "switch board se spark aa raha hai"],
    "noise": ["upar wale flat se raat ko bahut shor aata hai"],
    "parking": ["meri parking me koi gaadi khadi kar deta hai"],
    "lift": ["lift kharab hai, kal se band hai"],
    "security": ["raat ko gate pe guard nahi tha"],
    "cleanliness": ["kachra kayi din se nahi uthaya gaya"],
    "other": ["intercom kaam nahi kar raha"],
}
URGENT_PREFIX = ["URGENT: ", "Very urgent - ", "Emergency! "]
HIGH_SUFFIX = [". Please fix asap.", ". Needs immediate attention."]
LOW_SUFFIX = [". Not urgent, whenever possible.", ". Please look into it when free."]


def _typo(rng, text):
    chars = list(text)
    for _ in range(1 + int(rng.integers(0, 2))):
        if len(chars) < 5:
            break
        i = int(rng.integers(1, len(chars) - 2))
        if rng.random() < 0.5:
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
        else:
            del chars[i]
    return "".join(chars)


def make_text(rng, category, priority, tower, hinglish_prob, typo_prob):
    pool = HI[category] if rng.random() < hinglish_prob else EN[category]
    t = pool[int(rng.integers(len(pool)))]
    t = t.format(place=PLACES[int(rng.integers(len(PLACES)))], room=ROOMS[int(rng.integers(len(ROOMS)))],
                 n=int(rng.integers(2, 9)), tower=tower)
    if priority == "urgent" and rng.random() < 0.6:
        t = URGENT_PREFIX[int(rng.integers(len(URGENT_PREFIX)))] + t
    elif priority == "high" and rng.random() < 0.5:
        t += HIGH_SUFFIX[int(rng.integers(len(HIGH_SUFFIX)))]
    elif priority == "low" and rng.random() < 0.5:
        t += LOW_SUFFIX[int(rng.integers(len(LOW_SUFFIX)))]
    if rng.random() < typo_prob:
        t = _typo(rng, t)
    return t
