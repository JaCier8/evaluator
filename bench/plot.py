#!/usr/bin/env python3
"""Plots and a summary table from bench/results/*.csv.

Usage: python3 bench/plot.py      -> bench/results/*.png + bench/results/summary.md

Every point is the MEDIAN of the measured runs; the whisker spans min..max,
so outliers stay visible instead of being silently dropped.
"""
import csv
import os
import statistics
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE, MODEL = "#2a78d6", "#eb6834", "#8a8984"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 11, "axes.titleweight": "bold", "legend.frameon": False,
})


def load(name):
    path = os.path.join(RES, f"{name}.csv")
    if not os.path.exists(path):
        return None
    groups = defaultdict(list)
    model = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            key = (r["variant"], int(r["pol"]), int(r["cpu"]), int(r["envs"]))
            groups[key].append(float(r["throughput"]))
            model[key] = float(r["predicted"]) if r["predicted"] else None
    return {k: dict(med=statistics.median(v), lo=min(v), hi=max(v), n=len(v),
                    cv=100 * statistics.pstdev(v) / statistics.mean(v), model=model[k])
            for k, v in groups.items()}


def series(data, variant, axis):
    idx = {"pol": 1, "cpu": 2, "envs": 3}[axis]
    pts = sorted((k[idx], s) for k, s in data.items() if k[0] == variant)
    xs = [x for x, _ in pts]
    return xs, [s for _, s in pts]


def draw(ax, xs, stats, color, label, model=True, model_label="model"):
    med = [s["med"] for s in stats]
    err = [[m - s["lo"] for m, s in zip(med, stats)], [s["hi"] - m for m, s in zip(med, stats)]]
    if model and all(s["model"] is not None for s in stats):
        ax.plot(xs, [s["model"] for s in stats], ls="--", lw=1.5, color=MODEL if color == BLUE else color,
                alpha=0.9 if color == BLUE else 0.55, label=model_label, zorder=2)
    ax.errorbar(xs, med, yerr=err, color=color, lw=2, marker="o", ms=6,
                mec=SURFACE, mew=1.5, capsize=3, elinewidth=1.2, label=label, zorder=3)


def log2_x(ax, xs, title, xlabel):
    ax.set_xscale("log", base=2)
    ax.set_xticks(xs, [str(x) for x in xs])
    ax.minorticks_off()
    ax.set_title(title, loc="left")
    ax.set_xlabel(xlabel)
    ax.set_ylim(bottom=0)


def fig_sweeps():
    panels = [("cpu", "max_concurrent_calls  (argv[4])", "pol=16, envs=32"),
              ("pol", "max_concurrent_policy_calls  (argv[3])", "cpu=32, envs=32"),
              ("envs", "max_active_envs  (argv[5])", "pol=64, cpu=64")]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    for ax, (name, xlabel, fixed) in zip(axes, panels):
        data = load(name)
        if not data:
            continue
        xs, st = series(data, "early", name)
        draw(ax, xs, st, BLUE, "measured (median, min–max)")
        log2_x(ax, xs, f"Throughput vs {name} limit", xlabel)
        ax.text(0.02, 0.97, f"other limits: {fixed}", transform=ax.transAxes,
                va="top", color=INK2, fontsize=9)
    for ax in axes:
        ax.set_ylabel("throughput  [tests / s]")
    axes[0].legend(loc="center left", bbox_to_anchor=(0.0, 0.6))
    fig.tight_layout()
    fig.savefig(os.path.join(RES, "sweeps.png"), dpi=150)


def fig_ab():
    data = load("ab")
    if not data:
        return
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    for variant, color, label in [("early", BLUE, "A: early release (current code)"),
                                  ("late", ORANGE, "B: late release")]:
        xs, st = series(data, variant, "pol")
        draw(ax, xs, st, color, label, model_label=f"model, {variant}")
    log2_x(ax, xs, "A/B: when is sem_pol released?", "max_concurrent_policy_calls  (cpu=16, envs=32)")
    ax.set_ylabel("throughput  [tests / s]")
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(RES, "ab.png"), dpi=150)


def fig_busy():
    busy, sleep = load("busy"), load("cpu")
    if not busy:
        return
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    if sleep:
        xs, st = series(sleep, "early", "cpu")
        draw(ax, xs, st, BLUE, "sleep: env/policy only wait", model=False)
    xs, st = series(busy, "early", "cpu")
    draw(ax, xs, st, ORANGE, "busy: env/policy burn real CPU", model=False)
    cores = os.cpu_count()
    ax.axvline(cores, color=INK2, lw=1, ls=":")
    ax.text(cores * 1.05, ax.get_ylim()[1] * 0.95, f"{cores} cores on this machine",
            color=INK2, fontsize=9, va="top")
    log2_x(ax, sorted(set(xs) | {16}), "Sleep vs real CPU work",
           "max_concurrent_calls  (argv[4])")
    ax.set_ylabel("throughput  [tests / s]")
    ax.legend(loc="center right", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(RES, "busy.png"), dpi=150)


def summary():
    lines = ["| sweep | variant | pol | cpu | envs | runs | median tests/s | min | max | CV % | model | measured / model |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in ["cpu", "pol", "envs", "ab", "busy"]:
        data = load(name)
        if not data:
            continue
        for (v, p, c, e), s in sorted(data.items()):
            m = s["model"]
            lines.append(f"| {name} | {v} | {p} | {c} | {e} | {s['n']} | {s['med']:.1f} | {s['lo']:.1f} | "
                         f"{s['hi']:.1f} | {s['cv']:.1f} | {'' if m is None else f'{m:.0f}'} | "
                         f"{'' if m is None else f'{100 * s['med'] / m:.0f} %'} |")
    with open(os.path.join(RES, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    fig_sweeps()
    fig_ab()
    fig_busy()
    summary()
