# **Technical Analysis of Meta-Progression Systems in Elden Ring: Nightreign**

## **1\. Introduction to the Relic Paradigm**

The release of *Elden Ring: Nightreign* marks a significant departure from the traditional FromSoftware action-RPG progression model. While the base game relies on a linear accumulation of power through permanent equipment and unrestricted leveling, *Nightreign* adopts a roguelite framework where power is transient within the "Expedition" loop, yet anchored by a persistent meta-progression system known as the Relic System. This report provides an exhaustive technical analysis of the Relic system’s architecture, examining its role as the primary vector for buildcrafting, the mathematical intricacies of its scaling formulas, and the bifurcated design introduced by the *Deep of Night* expansion.

At a fundamental level, the Relic system serves to solve the "stat stick" problem inherent in converting a Souls-like into a roguelite. In a standard run, a player’s survival is dictated by skill and immediate gear drops. However, to provide a sense of long-term progression without trivializing the difficulty curve, the developers implemented Relics—modular, procedurally generated augmentations that modify the base parameters of the player's character, the "Nightfarer." Unlike standard equipment, Relics are not worn on the body but are socketed into "Vessels," creating a constrained optimization puzzle that forces players to balance offensive output, resource management, and survival utility within a limited slot configuration.

This analysis is intended for technical peers and systems designers seeking to replicate or deeply understand the mechanics of *Nightreign*. It synthesizes data from community datamining, extensive playtesting logs, and patch notes (up to version 1.03) to establish a definitive reference for the system’s logic, including stacking rules, RNG weighting, and the complex interaction between "Standard" and "Deep of Night" mechanics.

## **2\. Architectural Foundation: The Vessel System**

The structural backbone of the Relic system is the Vessel. It functions as the container logic for all equipped Relics, imposing hard constraints on player power through a system of color-coded slotting. This design prevents players from simply equipping the "best" items indiscriminately; instead, they must select a Vessel that aligns with their intended build archetype, accepting the limitations that come with that choice.

### **2.1 Vessel Taxonomy and Slot Configuration**

Every Nightfarer character begins with a default Vessel, typically classified as an "Urn." As players progress through Expeditions, defeat Nightlords, or interact with the economy at the Small Jar Bazaar, they unlock advanced Vessel types: Goblets, Chalices, and Grails. The distinction between these vessels is critical, as it defines the "Slot Configuration" available for Relic Rites.

The slotting system utilizes a color-coded abstraction that dictates the specific category of utility a character can access. This "Color Pie" philosophy ensures that no single Vessel can maximize every attribute simultaneously.

* **Red Slots (Burning):** These are strictly offensive. They accept Relics that boost physical attack power, skill damage, and critical hit modifiers. A Vessel heavy in Red slots promotes a "Glass Cannon" playstyle.1
* **Blue Slots (Drizzly):** These slots govern utility and resource management. They accept Relics that increase FP (Focus Points), boost Intelligence, or enhance Sorcery potency. They are essential for caster archetypes like the Duchess or Scholar.2
* **Green Slots (Tranquil):** These slots are defensive and stamina-oriented. They house Relics that improve Guard Stability, Stamina Recovery, and Damage Negation. A build utilizing Green slots prioritizes survivability and sustained engagement over burst damage.2
* **Yellow Slots (Luminous):** These are specialized slots, often associated with Faith, Holy damage, or unique mechanical interactions such as the "Night Invader" bonuses. They serve as the home for hybrid utility and buff-oriented effects.2
* **White Slots (Universal):** These are "Wildcard" slots that can accept Relics of any color. Vessels containing White slots, such as the *Sacred Erdtree Grail*, are highly prized for their flexibility, allowing players to break the standard archetype molds.2
* **Black Slots (Special):** Found primarily on high-tier Remembrance Chalices, these slots are reserved for unique or "Fetish" class relics that offer game-changing passive effects often derived from boss mechanics.2

### **2.2 Nightfarer-Specific Vessel Configurations**

The distribution of slots is not random; it is tailored to enforce class identity while offering divergent build paths through unlockable Vessels. The following table details the known slot configurations for key Nightfarers, highlighting the shift from Standard play to the expanded *Deep of Night* (DoN) mode.

**Table 1: Detailed Vessel Slot Configurations**

| Nightfarer | Vessel Type | Source | Standard Slots | Deep of Night Slots | Strategic Implication |
| :---- | :---- | :---- | :---- | :---- | :---- |
| **Wylder** | Wylder's Urn (Default) | Starting Gear | Blue, Blue, Green | Red, Red, Yellow | Balanced starter; shifts to aggression in DoN. 3 |
|  | Wylder's Chalice | Remembrance | Blue, White, Black | Red, Yellow, Blue | High flexibility with a dedicated Special slot. 3 |
| **Raider** | Raider's Urn (Default) | Starting Gear | Blue, Yellow, Yellow | Red, Blue, Blue | Utility-focused start; DoN adds offensive capabilities. 3 |
|  | Raider's Chalice | Remembrance | Blue, Blue, Black | Red, Green, Green | Heavy resource management focus with Special utility. 3 |
| **Guardian** | Guardian's Urn (Default) | Starting Gear | Blue, White, White | Red, Green, Green | Extremely flexible starter due to double White slots. 3 |
|  | Guardian's Chalice | Remembrance | Green, White, Black | Red, Yellow, Green | shifts towards defensive capability (Green) \+ Special. 3 |
| **Executor** | Executor's Urn (Default) | Starting Gear | Blue, White, White | Red, Green, Green | Similar flexibility to Guardian; supports hybrid builds. 3 |
| **Universal** | Sacred Erdtree Grail | Bazaar | White, White, White | Green, Green, Green | Ultimate customization for Standard; defensive lock in DoN. 3 |
|  | Scadutree Grail | Default | Blue, Blue, Blue | Red, Red, Red | Pure utility in Standard; Pure aggression in DoN. 3 |

**Analysis of Vessel progression:**

The data reveals a clear design intent: "Default" vessels often provide a balanced or safe loadout (e.g., Wylder's Blue/Green mix for FP/Stamina), while "Remembrance" vessels (Chalices) introduce the Black slot, encouraging the use of Boss/Fetish relics. The "Universal" Grails offer the most radical shifts. For instance, the **Scadutree Grail** transforms a character from a pure caster (Blue/Blue/Blue) in Standard mode to a pure berserker (Red/Red/Red) in Deep of Night. This creates a dual-layer build strategy where a player must optimize their Standard loadout for the early game and their DoN loadout for the endgame difficulty spike.

### **2.3 The Relic Rite Mechanism**

The "Relic Rite" is the interaction method by which players commit Relics to these slots. Located at the Roundtable Hold or accessible via the main menu, the Rite is the bridge between the inventory and the active gameplay loop.

* **Binding Persistence:** Relics bound to a Vessel via the Rite remain there until removed. This allows players to maintain separate "Loadouts" for different characters without needing to constantly swap items.
* **The Murk Economy:** Unlocking new Vessels and maintaining a diverse Relic collection is driven by "Murk," the persistent currency. The economic cost of acquiring a specific Chalice (e.g., 1200 Murk for a Goblet) acts as a progression gate, ensuring that advanced slot configurations are earned through mastery of the core loop.4

## **3\. Relic Morphology and Generation Logic**

Unlike the fixed equipment of *Elden Ring*, the majority of *Nightreign's* Relics are procedurally generated. This system, colloquially referred to as "RNG" (Random Number Generation), utilizes a complex affix system to create thousands of potential combinations. Understanding the morphology of a Relic allows for immediate identification of its potential power level.

### **3.1 Naming Syntax and Tier Logic**

Relic names are constructed using a distinct syntax: **\[Prefix\] \+ \[Color/Adjective\] \+**. This structure is not merely flavor text; it is a code that indicates the item's tier, color alignment, and effect pool.

#### **3.1.1 Tier Prefixes**

The prefix determines the "Density" of effects on the Relic—essentially, how many distinct lines of text or how potent the primary stat boost is.

* **Delicate (Tier 1):** These Relics typically contain a single effect or a low-magnitude stat boost (e.g., \+1 Strength). They are common drops in early Expeditions and cheap purchases at the Bazaar.
* **Polished (Tier 2):** These Relics usually contain two distinct effects or a moderate stat boost (e.g., \+2 Strength). They represent the mid-game standard.
* **Grand (Tier 3):** The highest tier of standard procedural loot. A "Grand" Relic can hold up to three distinct effects or high-magnitude stat boosts (e.g., \+3 Strength). Securing "Grand" Relics with synergistic effects is the primary endgame goal for min-maxers.5

#### **3.1.2 Color Identifiers**

The second word in the name corresponds directly to the Vessel slot color required to equip the item.

* **Tranquil:** Green (Defensive/Stamina).
* **Drizzly:** Blue (FP/Intelligence/Sorcery).
* **Luminous:** Yellow (Faith/Special/Holy).
* **Burning:** Red (Attack/Skill/Crit).

#### **3.1.3 Suffix Indicators**

The suffix (e.g., "Scene," "Whetstone," "Tear") dictates the icon and often correlates with specific sub-pools of effects. For example, "Whetstone" relics are almost exclusively Red (Burning) and carry physical or critical damage properties, while "Tear" relics are often Blue (Drizzly) and relate to FP or Magic.7

### **3.2 The Fetish Category: Fixed-Stat Relics**

Beyond the procedural pool, *Nightreign* includes a category of "Unique" or "Fetish" Relics. These items do not follow the random affix rules and always possess predetermined stats. They are typically rewards for defeating Nightlords, completing Remembrance quests, or finding specific secrets.

* **Fell Omen Fetish:** A prime example of this category. It provides a unique set of bonuses related to the Omen curse, often involving attack power boosts or specific interaction with Omen-type enemies. Because its stats are fixed, it serves as a reliable anchor for builds that cannot rely on RNG.8
* **Executor’s DoN Relic:** This unique relic has a notable property discovered by the community: **Self-Stacking**. While most procedural relics follow complex stacking rules, specific unique relics like this one have been observed to stack with themselves if a player manages to acquire duplicates (e.g., through New Game+ cycles or bugs). This allows for linear scaling of unique effects, such as HP regeneration triggers.9

## **4\. The Mathematics of Power: Scaling and Stacking**

To fully understand the Relic system, one must dissect the mathematical framework governing stats and damage. *Nightreign* operates on a compressed level curve compared to the base game, fundamentally altering the value of stat points.

### **4.1 The Level 1-15 Compression Curve**

In *Elden Ring*, stats scale up to 99, with soft caps at 40, 60, and 80\. In *Nightreign*, the level cap is strictly set at **15**. This compression means that every single stat point represents a massive percentage of a character's total power budget.

* **Stat Levels vs. Character Levels:** Relics that grant **\+1, \+2, or \+3** to a stat (e.g., Strength) are adding "Stat Levels."
* **The "Zero-to-One" Spike:** Datamining reveals that the initial level-up (Level 1 to 2\) provides the most significant spike in base attributes. For example, the **Wylder** class gains approximately **\+13 Strength** on their first level-up, whereas subsequent levels provide a linear gain of only **\+2 to \+3 points**.10
* **Implication for Relics:** A **\+3 Strength** Relic is incredibly potent at Level 1, as it simulates multiple levels of growth. However, at Level 15, if a character has hit the internal soft cap for Strength (approximately 50 in *Nightreign* logic), the relative value of a \+3 Relic diminishes due to linear scaling limits.12

### **4.2 Damage Calculation Formulas**

The community has reverse-engineered the damage formulas to determine how Relic effects interact. The distinction between **Additive** and **Multiplicative** stacking is the single most important factor in high-level buildcrafting.

#### **4.2.1 Multiplicative Damage Scaling**

The majority of **Damage Dealt** modifiers in *Nightreign* stack multiplicatively. This effectively means that diversifying damage sources yields exponential returns.

* **Formula:** Final Damage \= Base Damage \* (1 \+ Buff A) \* (1 \+ Buff B) \*...
* **Example:** A player equips a "Physical Attack Up" (+10%) relic and a "Skill Damage Up" (+15%) relic.
  * *Calculation:* 1.00 \* 1.10 \* 1.15 \= 1.265 (+26.5% Total Damage).
  * *Contrast:* If these were additive, the result would be 1.00 \+ 0.10 \+ 0.15 \= 1.25 (+25% Total Damage).
* **Significance:** As more unique multipliers are added (e.g., "Damage vs. Poisoned Enemies," "Critical Hit Damage," "Night Invader Bonus"), the gap between multiplicative and additive scaling widens drastically. This incentivizes "Rainbow Builds" that utilize multiple different *types* of damage bonuses rather than stacking a single type.13

#### **4.2.2 Additive Negation Mechanics**

Conversely, **Damage Negation** (Defense) generally stacks additively, with specific diminishing return rules to prevent immunity.

* **The "Different Value" Rule:** A relic providing 10% Negation and another providing 12% Negation will typically stack. However, two identical "10% Negation" relics often conflict, resulting in only one applying or a severe penalty to the second's efficacy.
* **Threshold Exceptions:** Passives that trigger at specific HP thresholds (e.g., "Damage Negation at Full HP") function as independent layers. They stack on top of base negation without conflict, making them premium defensive choices.9

### **4.3 Specific Relic Values (Patch 1.03)**

Recent patches have adjusted the values of many standard Relic effects. The following table provides the confirmed percentage values for key offensive properties, essential for players attempting to calculate their theoretical maximums.

**Table 2: Relic Effect Values (Standard vs. Upgraded)**

| Effect Name | Base (+0) | \+1 Version | \+2 Version | \+3 Version | \+4 Version | Notes |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| **Physical Attack Up** | \+4% | \+5% | \+6% | \+10.5% | \+12% | Multiplicative. 14 |
| **Improved Critical Hits** | \+18% | \+23% | \+29% | \- | \- | Multiplies Final Dmg. 14 |
| **Guard Counter Power** | \+23% | \+25% | \+29% | \- | \- | Massive buff in 1.03. 14 |
| **Melee Atk (Weapon Passive)** | \+9% | \+12% | \+15% | \- | \- | Specific to weapon slots. 14 |
| **Skill Attack Power** | \+15% | \- | \- | \- | \- | Key for Ash of War builds. 14 |

**Insight:** The "Improved Critical Hits" relic is particularly notable because it boosts the *final damage number*, not just the weapon's critical motion value. This makes it a multiplicative finisher that scales aggressively with high-damage weapons like Colossals or Daggers.15

### **4.4 Status Effect Scaling: The Arcane Anomaly**

A critical deviation in *Nightreign* is the unification of status scaling. In base *Elden Ring*, Frostbite scales with Intelligence and Weapon Level. In *Nightreign*, **all status effect buildups (Poison, Rot, Bleed, Frost) scale with Arcane.**

* **The Static Stat Problem:** Unlike Strength or Dexterity, **Arcane (ARC)** does not increase naturally with Character Level for most Nightfarers. It remains static throughout the run unless modified by external sources.
* **Relic Dependency:** Because Arcane does not grow, Relics are the *only* vector for scaling status buildup. A **\+3 Arcane** Relic is therefore mandatory for any status-focused build, as it represents the only way to reach proc thresholds required to debilitate late-game Nightlords.16

## **5\. The Deep of Night: Curse Mechanics and Bifurcation**

The *Deep of Night* (DoN) expansion introduced a fundamental bifurcation in the Relic system. While Standard Relics function in all game modes, **Depths Relics** are exclusively active during DoN Expeditions. This separation is enforced by the expansion of the Vessel system to include three additional "Deep" slots, bringing the total potential Relic loadout to six (3 Standard \+ 3 Deep).

### **5.1 The Logic of Curses**

The defining characteristic of Depths Relics is the "Curse" system. Unlike Standard Relics, which provide strictly positive benefits, Depths Relics operate on a "Great Power at a Cost" philosophy. Every Depths Relic pairs a significant mechanical or statistical buff with a corresponding detriment. This forces players into a high-stakes risk management game.

**Taxonomy of Curses:**

1. **Stat Trade-offs:** The most prevalent form of curse involves boosting one Attribute while reducing another.
   * *Example:* \*\* Improved Arcane / Reduced Vigor.\*\* This creates a "Glass Cannon" dynamic where damage potential (via Arcane-scaled status effects) increases at the direct expense of survivability.
   * *Example:* \*\* Improved Intelligence & Faith / Reduced Mind.\*\* This forces a caster to manage a smaller FP pool despite having more potent spells, requiring precise resource management or the use of Cerulean Flask relics.17
   * *Example:* \*\* Improved Int/Faith / Reduced Str/Dex.\*\* This effectively forcibly converts a melee character into a hybrid caster, altering the fundamental way the class is played.17
2. **Mechanical Detriments:** Some relics impose gameplay modifications rather than simple stat reductions.
   * *Example:* **Increased Damage Taken after Evasion.** This punishes panic-rolling. If a player dodges and is caught by a roll-catch attack, they suffer amplified damage.
   * *Example:* **Reduced Flask Efficacy.** This curse lowers the HP or FP restored by flasks, demanding that players avoid damage entirely rather than tanking and healing.18
3. **Meta-Curses:**
   * *Example:* **Collect Affinity Residues to Negate Affinity.** This complex curse requires players to actively engage with a collection mechanic during the run to offset a penalty, adding a layer of cognitive load to exploration.17

### **5.2 Strategic Integration: The Mitigation Strategy**

The separation of Standard and Deep slots allows for compensatory strategies. A player utilizes their Standard slots to mitigate the curses of their Deep slots.

* **Nullification:** If a Deep Relic reduces **Vigor by \-3** (approx. \-60 HP at Level 15), a Standard "Grand Tranquil" Relic providing **Vigor \+3** can be slotted to neutralize the penalty. This effectively isolates the Deep Relic's buff (e.g., Improved Arcane) at the cost of one Standard slot.
* **Min-Maxing:** Advanced players often lean into the curses. A "No Hit" runner utilizing the **Revenant** class might accept a "Reduced Vigor" curse because they do not intend to take damage, thereby gaining the offensive benefit for "free" in practical terms. Conversely, a tank build might accept "Reduced Intelligence" (a dump stat) to gain massive boosts to Guard Stability.

## **6\. The Dormant Power Subsystem**

"Dormant Power" is a specific relic affix that does not provide direct combat stats but instead manipulates the game's RNG loot tables. This system is frequently misunderstood by players as a drop *rate* increase, but rigorous analysis confirms it is a **Drop Pool Replacement** mechanic.

### **6.1 Mechanism of Action: Pool Replacement**

Every Nightfarer has a "Base Dormant Power" inherent to their class. For example, **Wylder** has a base bias towards discovering **Greatswords**, while **Ironeye** discovers **Bows**. This corresponds to a roughly **35% base chance** that a weapon drop will be of that specific class.

* **The Replacement Rule:** Equipping a Relic with the affix "Dormant Power helps discover" **replaces** the character's base 35% chance with the new weapon type. It does **not** add to it.
  * *Scenario:* A Wylder equips "Dormant Power: Katanas."
  * *Result:* They lose their Greatsword bias and gain a Katana bias. They do **not** have a 35% chance for Greatswords AND a 35% chance for Katanas. The relic overrides the native class trait.
* **Stacking Futility:** Equipping two identical Dormant Power relics (e.g., two "Discover Katana" relics) does **not** increase the probability to 70%. The second relic is functionally dead code. Players should never stack identical Dormant Powers.19

### **6.2 Loot Rarity and Enemy Tier Logic**

The probability of receiving a Dormant Power weapon is heavily weighted by the source of the drop (The Enemy Tier). This knowledge is crucial for routing Expeditions to maximize the chance of securing a build-defining weapon.

**Table 3: Dormant Power Drop Probabilities**

| Enemy Tier | Drop Probability | Typical Rarity |
| :---- | :---- | :---- |
| **Fort / Great Church Bosses** | **65%** | Uncommon (Blue) |
| **Ruins / Main Encampment Bosses** | 35% | Uncommon (Blue) |
| **Formidable Field Bosses** | 35% | Rare (Purple) |
| **Castle Elite Enemies** | 25% | Uncommon (Blue) |
| **Nightlords / Legends (e.g., Astel)** | 15% | Legendary (Orange) |

**Analysis:** This distribution indicates that Dormant Power relics are most effective in the early-to-mid game (Forts/Ruins) for securing a weapon. Their efficacy drops drastically against end-game bosses (15%), meaning players cannot rely on them to farm specific Legendary weapons from Nightlords. The strategy, therefore, is to use Dormant Power to secure a solid "Blue" or "Purple" weapon early, then adapt to whatever Legendary drops naturally.19

## **7\. Economic Systems: Murk and Gambling**

The Relic system is fueled by **Murk**, the persistent currency retained between runs. The economy is designed to facilitate a transition from "Scavenging" to "Targeted Buildcrafting."

### **7.1 The Small Jar Bazaar**

Located in the Roundtable Hold, the Bazaar is the primary sink for Murk.

* **Cost Scaling:** Initial Relic purchases are cheap (approx. 500 Murk), allowing for early power spikes.
* **Vessel Upgrades:** The purchase of Goblets (1200 Murk) is the first major economic hurdle. This investment is generally more valuable than buying random relics early on, as it unlocks the color flexibility needed to equip better drops found in the wild.4

### **7.2 The "Scenic Flatstone" Gambling Loop**

The endgame of the Relic system revolves around **Scenic Flatstones**. These items function as "Unidentified Relics" or a "Gacha" mechanic.

* **The Gambling Loop:** Players purchase Flatstones in bulk and "reveal" them. This is the only way to effectively grind for specific "Grand" tier relics with perfect affix combinations (e.g., Grand Burning Scene with \+Physical, \+Crit, \+Stamina).
* **RNG Weights:** While exact drop rates are obfuscated, community data suggests that "Grand" (Tier 3\) relics have a significantly lower roll probability from Flatstones compared to high-level Expedition rewards. However, the Bazaar allows for "brute-forcing" the RNG through volume. Players often spend millions of Murk rolling Flatstones to find a single "God Roll" relic.20

## **8\. Advanced Archetype Synergies**

By combining the mathematical stacking rules with specific unique relics, players have identified several "Meta" archetypes that outperform standard play.

### **8.1 The "Balancer" Infinite Scaling Archetype**

One of the most potent interactions discovered involves the **Balancer Relics** (Will of Balance).

* **Effect:** Increases Skill Attack Power and Melee Attack Power.
* **Stacking Anomaly:** Uniquely, the Standard and Everdark versions of the Balancer relics stack **multiplicatively** with each other.
* **Synergy:** When combined with "Successive Attack" relics (e.g., on the **Executor** class, who specializes in rapid hits), this setup can yield nearly **100% increased Ash of War damage**. This creates a hyper-carry build focused on "Ash of War Spam" (e.g., Rivers of Blood), bypassing the need for standard melee scaling entirely.21

### **8.2 The "Low HP" Immortal Archetype**

Relics offering "Damage Negation at Low HP" have been a subject of intense scrutiny.

* **The Mechanic:** These relics provide massive damage reduction (e.g., 40%) when HP falls below a threshold (e.g., 40%).
* **DoN Synergy:** Paradoxically, the DoN curses that reduce **Max HP** (e.g., \-3 Vigor) synergize with this. By lowering Max HP, it becomes easier to sustain the character within the "Low HP" percentage threshold using fixed-value healing items, maintaining the defensive buff permanently. This turns a "Glass Cannon" curse into a "Tank" benefit through mathematical manipulation of HP ratios.14

## **9\. Conclusion**

The Relic system in *Elden Ring: Nightreign* is a sophisticated engine of meta-progression that fundamentally alters the way players approach the game's combat. By moving power from the character's body to the Vessel's slots, FromSoftware has created a modular difficulty system. The **Vessel** acts as the constraint, the **Relic** acts as the variable, and the **Rite** acts as the commitment.

The introduction of **Deep of Night** curses adds a layer of "Min-Max" philosophy typically reserved for complex CRPGs, forcing action-game players to engage with statistical trade-offs. Meanwhile, the **Dormant Power** system provides a deterministic safety valve for the inherent randomness of the roguelite genre. Mastering *Nightreign* is not just about dodging attacks; it is about understanding the multiplicative nature of damage, the opportunity cost of Vessel slots, and the statistical manipulation of RNG tables to construct a Nightfarer capable of surviving the infinite loop.
