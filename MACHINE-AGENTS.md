# AGENTS.md — développement sur cette machine

Machine personnelle, utilisée **en parallèle du jeu et d'autres applications**.
Les règles ci-dessous ne sont pas des préférences de style : chacune vient d'un
incident mesuré sur cette machine. Les ignorer a déjà gelé l'interface.

La machine est partagée : un build ne doit jamais avoir la priorité sur ce que
l'utilisateur est en train de faire.

---

## 1. Matériel — à ne pas re-deviner

| Ressource | Valeur |
|---|---|
| CPU | Intel i5-13500, hybride : **6 P-cores** (LP 0–11, masque `0x00FFF`) + **8 E-cores** (LP 12–19, masque `0xFF000`) |
| RAM | 31,7 Go physiques |
| Limite de commit | ~68,6 Go (RAM + pagefile automatique) |
| GPU | RTX 4070 Ti, 12 Go de VRAM |
| Disque système | Samsung 980 PRO NVMe |

Les jeux et l'interface veulent les P-cores. Les compilations n'ont aucune
contrainte de latence : elles vont sur les E-cores.

---

## 2. Builds Rust

1. **Ne jamais lancer `cargo` directement** pour un build de fond. Utiliser le
   wrapper, déjà dans le `PATH` :

   ```
   cargo-bg build
   cargo-bg test --release
   ```

   `cargo-bg` (`~/.local/bin/cargo-bg.ps1`) parke tout le sous-arbre
   `cargo`/`rustc`/`link` sur les 8 E-cores et le passe en `BelowNormal`.
   L'affinité est héritée par les enfants, donc un seul appel suffit.

2. **Un seul `cargo` à la fois.** Lancer plusieurs `cargo` en parallèle multiplie
   les jobs sans aucune coordination entre eux. C'est la cause racine de
   l'incident du 2026-11-08 : 6 `cargo` → 40 `rustc` simultanés.

3. `~/.cargo/config.toml` plafonne `build.jobs = 6`. C'est délibéré : à 6 jobs le
   pic de RSS d'un build tourne autour de 2 Go, contre ~10 Go sans plafond.
   Ne pas relever ce plafond pour « aller plus vite ». Si un build plein pot est
   vraiment nécessaire, le faire explicitement et **sans jeu lancé** :
   `cargo build -j 14`.

4. Ne pas tuer des `rustc` au hasard pour libérer de la mémoire : ça casse le
   build sans rendre la mémoire durablement. Réduire les jobs, ou baisser la
   priorité, donne le même résultat sans rien casser.

---

## 3. Lenteur ou gel de l'interface : quoi regarder

**Le CPU n'est presque jamais le coupable ici.** Il tournait à 20–50 % pendant
les gels les plus sévères. L'ordre de diagnostic :

1. **`pages/s`** — le seul chiffre qui compte vraiment.

   ```powershell
   (Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory).PagesPerSec
   ```

   - `< 1 000` : normal
   - `> 2 000` : pagination sévère, l'interface va saccader
   - `~ 20 000` : c'est un gel en cours

2. **Commit** — `CommittedBytes / CommitLimit`. Rester sous 80 %. À 97 %, la
   prochaine allocation échoue au lieu de simplement ralentir.

   ```powershell
   $m = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
   "{0:N1} / {1:N1} Go ({2:N0} %)" -f ($m.CommittedBytes/1GB), ($m.CommitLimit/1GB), ($m.CommittedBytes/$m.CommitLimit*100)
   ```

3. **Nombre de process `rustc` simultanés** — au-delà de 8, les jobs ne sont pas
   plafonnés quelque part.

À ne pas confondre avec un problème de driver : `PercentDPCTime` et
`PercentInterruptTime` sont restés à 0–1 % pendant tous les incidents, et le
disque (NVMe) n'a jamais saturé. Ce n'est ni le GPU, ni l'audio, ni l'I/O.

---

## 4. Historique

**2026-11-08** — Gels répétés de l'interface. Cause : 6 `cargo` en parallèle,
40 `rustc`, 9,7 Go de RSS, commit à 97 % de la limite, ~22 000 pages/s de
pagination. Le pagefile s'est étendu tout seul de 38,5 à 41,6 Go.
Résolution : priorité `BelowNormal` sur `rustc`/`cargo`/`link` puis fin du build.
D'où les règles de la section 2.
