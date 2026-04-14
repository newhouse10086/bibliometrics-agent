"""BurstDetector module — Detect sudden frequency spikes in keyword-year data.

Reference: sample-project/Burst_Detection.py
Algorithm: Kleinberg burst detection (or fallback threshold method)
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib import rcParams

from modules.base import BaseModule, HardwareSpec, RunContext

logger = logging.getLogger(__name__)

# Try to import burst_detection library
try:
    import burst_detection as bd
    BD_AVAILABLE = True
except ImportError:
    BD_AVAILABLE = False
    logger.warning("burst_detection library not available, will use alternative method")


class BurstDetector(BaseModule):
    """Burst detection for keyword frequency time series."""

    @property
    def name(self) -> str:
        return "burst_detector"

    @property
    def version(self) -> str:
        return "0.1.0"

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "required": ["keyword_year_matrix_path"],
            "properties": {
                "keyword_year_matrix_path": {
                    "type": "string",
                    "description": "Path to keyword-year frequency matrix (CSV or Excel)",
                },
                "year_column": {
                    "type": "string",
                    "default": "Year",
                    "description": "Column name for years (if using long format)",
                },
            },
        }

    def output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "burst_results_path": {
                    "type": "string",
                    "description": "Path to burst detection results CSV",
                },
                "visualizations_dir": {
                    "type": "string",
                    "description": "Directory containing burst visualization plots",
                },
                "summary": {
                    "type": "object",
                    "properties": {
                        "total_keywords": {"type": "integer"},
                        "keywords_with_bursts": {"type": "integer"},
                        "total_bursts": {"type": "integer"},
                    },
                },
            },
        }

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "threshold_multiplier": {
                    "type": "number",
                    "default": 1.5,
                    "description": "Threshold multiplier for burst detection",
                },
                "min_burst_duration": {
                    "type": "integer",
                    "default": 2,
                    "description": "Minimum burst duration in time units",
                },
                "frequency_threshold_percentile": {
                    "type": "number",
                    "default": 0.5,
                    "description": "Percentile threshold for keyword filtering (0-1)",
                },
                "use_kleinberg": {
                    "type": "boolean",
                    "default": True,
                    "description": "Prefer Kleinberg algorithm if available",
                },
                "smooth_window": {
                    "type": "integer",
                    "default": 3,
                    "description": "Smoothing window size for time series",
                },
            },
        }

    def get_hardware_requirements(self, config: dict) -> HardwareSpec:
        """Burst detection is computationally light."""
        return HardwareSpec(
            cpu_cores=1,
            min_memory_gb=2.0,
            recommended_memory_gb=4.0,
            gpu_required=False,
            estimated_runtime_seconds=300,  # 5 minutes
        )

    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        """Run burst detection on keyword-year matrix."""
        logger.info("Starting burst detection analysis")

        # Load keyword-year matrix - try input_data first, then check previous_outputs
        if "keyword_year_matrix_path" in input_data:
            matrix_path = Path(input_data["keyword_year_matrix_path"])
        elif "frequency_analyzer" in context.previous_outputs:
            # Get from frequency_analyzer's output
            matrix_path = Path(context.previous_outputs["frequency_analyzer"]["keyword_year_matrix_path"])
        else:
            raise KeyError("keyword_year_matrix_path not found in input_data or previous_outputs from frequency_analyzer")
        if matrix_path.suffix == ".xlsx":
            df = pd.read_excel(matrix_path, index_col=0)
        else:
            df = pd.read_csv(matrix_path, index_col=0)

        logger.info(f"Loaded matrix with shape {df.shape}")

        # Normalize data
        df_normalized = df.div(df.sum(axis=1), axis=0)

        # Filter keywords by total frequency
        threshold = df.sum(axis=1).quantile(config.get("frequency_threshold_percentile", 0.5))
        keywords_above_threshold = df[df.sum(axis=1) > threshold].index.tolist()
        logger.info(f"Processing {len(keywords_above_threshold)} keywords above threshold")

        # Prepare time points
        timepoints = df.columns.values
        d = df.sum().values  # Total counts per year
        d = np.where(d == 0, 1e-10, d)  # Avoid division by zero

        # Output directories
        output_dir = context.get_output_path(self.name, "")
        viz_dir = output_dir / "visualizations"
        viz_dir.mkdir(parents=True, exist_ok=True)

        # Results storage
        all_bursts = []
        summary = {
            "total_keywords": 0,
            "keywords_with_bursts": 0,
            "total_bursts": 0,
        }

        # Process each keyword
        for idx, keyword in enumerate(keywords_above_threshold):
            logger.info(f"Processing keyword {idx+1}/{len(keywords_above_threshold)}: {keyword}")

            try:
                # Get time series
                r = df_normalized.loc[keyword].values
                r = np.array(r).flatten()

                # Detect bursts
                q, bursts, used_kleinberg = self._detect_bursts(
                    r=r,
                    d=d,
                    timepoints=timepoints,
                    keyword=keyword,
                    config=config,
                )

                # Update summary
                summary["total_keywords"] += 1
                if len(bursts) > 0:
                    summary["keywords_with_bursts"] += 1
                    summary["total_bursts"] += len(bursts)

                    # Add keyword name to bursts
                    bursts["keyword"] = keyword
                    all_bursts.append(bursts)

                # Generate visualization
                self._plot_burst_timeline(
                    bursts=bursts,
                    keyword=keyword,
                    q=q,
                    r=r,
                    timepoints=timepoints,
                    used_kleinberg=used_kleinberg,
                    output_dir=viz_dir,
                )

                logger.info(f"  Found {len(bursts)} bursts for {keyword}")

            except Exception as e:
                logger.error(f"Error processing keyword {keyword}: {e}")
                continue

        # Combine all burst results
        if all_bursts:
            bursts_df = pd.concat(all_bursts, ignore_index=True)
        else:
            bursts_df = pd.DataFrame(columns=["keyword", "begin", "end", "duration", "intensity"])

        # Save results
        bursts_path = output_dir / "burst_results.csv"

        # Ensure directory exists before saving (Windows compatibility)
        bursts_path.parent.mkdir(parents=True, exist_ok=True)

        bursts_df.to_csv(bursts_path, index=False)
        logger.info(f"Saved burst results to {bursts_path}")

        # Save summary
        summary_path = output_dir / "burst_summary.txt"
        with open(summary_path, "w") as f:
            f.write(f"Burst Detection Summary\n")
            f.write(f"========================\n\n")
            f.write(f"Total keywords analyzed: {summary['total_keywords']}\n")
            f.write(f"Keywords with bursts: {summary['keywords_with_bursts']}\n")
            f.write(f"Total bursts detected: {summary['total_bursts']}\n")
            if summary['total_keywords'] > 0:
                pct = 100 * summary['keywords_with_bursts'] / summary['total_keywords']
                f.write(f"Percentage with bursts: {pct:.1f}%\n")

        return {
            "burst_results_path": str(bursts_path),
            "visualizations_dir": str(viz_dir),
            "summary": summary,
        }

    def _detect_bursts(
        self,
        r: np.ndarray,
        d: np.ndarray,
        timepoints: np.ndarray,
        keyword: str,
        config: dict,
    ) -> tuple[np.ndarray, pd.DataFrame, bool]:
        """Detect bursts using Kleinberg or fallback method."""
        use_kleinberg = config.get("use_kleinberg", True) and BD_AVAILABLE

        if use_kleinberg:
            try:
                # Ensure timepoints are sequential integers
                if not np.all(np.diff(timepoints) == 1):
                    timepoints_int = np.arange(len(timepoints))
                else:
                    timepoints_int = timepoints.astype(int)

                # Run Kleinberg burst detection
                s = config.get("kleinberg_s", 1.5)
                gamma = config.get("kleinberg_gamma", 1.0)
                smooth_win = config.get("smooth_window", 2)

                [q, _, _, p] = bd.burst_detection(r, d, timepoints_int, s=s, gamma=gamma, smooth_win=smooth_win)

                # Enumerate bursts
                bursts = bd.enumerate_bursts(q, keyword)

                if len(bursts) > 0:
                    # Calculate burst weights
                    bursts = bd.burst_weights(bursts, r, d, p)

                logger.info(f"Kleinberg burst detection successful for {keyword}")
                return q, bursts, True

            except Exception as e:
                logger.warning(f"Kleinberg burst detection failed for {keyword}: {e}, using fallback")

        # Fallback: simple threshold-based detection
        q, bursts = self._simple_burst_detection(
            r=r,
            threshold_multiplier=config.get("threshold_multiplier", 1.5),
            min_duration=config.get("min_burst_duration", 2),
        )

        return q, bursts, False

    def _simple_burst_detection(
        self,
        r: np.ndarray,
        threshold_multiplier: float = 1.5,
        min_duration: int = 2,
        _depth: int = 0,
    ) -> tuple[np.ndarray, pd.DataFrame]:
        """Simple threshold-based burst detection."""
        # Calculate statistics
        mean_val = np.mean(r)
        std_val = np.std(r)
        median_val = np.median(r)
        percentile_75 = np.percentile(r, 75)

        # Multiple threshold strategies
        threshold_1 = mean_val + threshold_multiplier * std_val
        threshold_2 = median_val + threshold_multiplier * std_val
        threshold_3 = percentile_75
        threshold_4 = mean_val + 0.5 * (np.max(r) - mean_val)

        # Select most sensitive threshold
        threshold = min(threshold_1, threshold_2, max(threshold_3, mean_val + 0.5 * std_val))

        # Identify points above threshold
        above_threshold = r > threshold

        # Merge continuous burst periods
        q = np.zeros_like(r, dtype=int)
        burst_periods = []

        i = 0
        while i < len(above_threshold):
            if above_threshold[i]:
                start = i
                while i < len(above_threshold) and above_threshold[i]:
                    i += 1
                end = i - 1

                # Check duration
                if end - start + 1 >= min_duration:
                    q[start:end+1] = 1
                    burst_periods.append({
                        "begin": start,
                        "end": end,
                        "duration": end - start + 1,
                        "intensity": float(np.mean(r[start:end+1])),
                        "max_intensity": float(np.max(r[start:end+1])),
                    })
            else:
                i += 1

        # If no bursts found, try more relaxed conditions (max 2 attempts)
        if len(burst_periods) == 0 and threshold_multiplier > 0.5 and _depth < 2:
            return self._simple_burst_detection(r, threshold_multiplier=0.8, min_duration=1, _depth=_depth + 1)

        bursts = pd.DataFrame(burst_periods) if burst_periods else pd.DataFrame(
            columns=["begin", "end", "duration", "intensity", "max_intensity"]
        )

        return q, bursts

    def _plot_burst_timeline(
        self,
        bursts: pd.DataFrame,
        keyword: str,
        q: np.ndarray,
        r: np.ndarray,
        timepoints: np.ndarray,
        used_kleinberg: bool,
        output_dir: Path,
    ) -> None:
        """Generate burst visualization plot."""
        sns.set_style("white")
        rcParams['font.size'] = 14

        safe_keyword = keyword.replace("/", "_").replace(":", "_")

        # Ensure timepoints are numeric
        try:
            timepoints_numeric = pd.to_numeric(timepoints, errors='coerce')
            if np.any(pd.isna(timepoints_numeric)):
                # If conversion fails, use sequential integers
                timepoints_numeric = np.arange(len(timepoints))
        except Exception:
            timepoints_numeric = np.arange(len(timepoints))

        fig, axes = plt.subplots(3, 1, figsize=(14, 10))

        # Subplot 1: Original data
        axes[0].plot(timepoints_numeric, r, 'o-', color='blue', linewidth=2, markersize=6)
        axes[0].set_ylabel('Normalized\nFrequency')
        axes[0].set_title(f'Data and Burst Detection for {keyword}')
        axes[0].grid(True, alpha=0.3)

        # Subplot 2: Burst state
        axes[1].plot(timepoints_numeric, q, 's-', color='red', linewidth=2, markersize=8)
        axes[1].set_ylabel('Burst State\n(0=Normal, 1=Burst)')
        axes[1].set_ylim(-0.1, 1.1)
        axes[1].grid(True, alpha=0.3)

        # Subplot 3: Burst timeline
        if len(bursts) == 0:
            axes[2].text(0.5, 0.5, 'No bursts detected',
                        ha='center', va='center', transform=axes[2].transAxes,
                        fontsize=14, bbox=dict(boxstyle="round,pad=0.3", facecolor="lightgray"))
            axes[2].set_ylabel('Burst Timeline')
        else:
            year_step = int(np.median(np.diff(timepoints_numeric))) if len(timepoints_numeric) > 1 else 1
            axes[2].set_xlim(timepoints_numeric[0] - 0.5, timepoints_numeric[-1] + year_step + 0.5)
            axes[2].set_ylim(0, 1)

            for idx, burst in bursts.iterrows():
                y = 0.3
                begin_idx = int(burst['begin'])
                end_idx = int(burst['end'])
                begin_idx = max(0, min(begin_idx, len(timepoints_numeric)-1))
                end_idx = max(0, min(end_idx, len(timepoints_numeric)-1))

                xstart = timepoints_numeric[begin_idx]
                xend = timepoints_numeric[end_idx]
                width = xend - xstart + 1

                rect = plt.Rectangle((xstart, y), width, 0.4,
                                   facecolor='#00bbcc', edgecolor='black',
                                   linewidth=2, alpha=0.7)
                axes[2].add_patch(rect)
                axes[2].text(xstart + width/2, y + 0.2, f"B{idx+1}",
                            ha='center', va='center', fontsize=10, fontweight='bold')

            axes[2].set_ylabel('Burst Timeline')

            burst_info = f"{len(bursts)} burst(s) detected"
            if len(bursts) > 0:
                avg_duration = bursts['duration'].mean()
                burst_info += f"\nAvg duration: {avg_duration:.1f} years"

            axes[2].text(0.02, 0.98, burst_info, transform=axes[2].transAxes,
                        verticalalignment='top', bbox=dict(boxstyle="round,pad=0.3",
                        facecolor="lightyellow", alpha=0.8))

        # X-axis formatting
        for ax in axes:
            ax.set_xticks(timepoints_numeric[::max(1, len(timepoints_numeric)//10)])
            ax.tick_params(axis='x', rotation=45)

        axes[2].set_xlabel('Year')

        method_text = "Kleinberg burst_detection" if used_kleinberg else "Alternative threshold method"
        fig.suptitle(f'Burst Analysis: {keyword}\n(Method: {method_text})', fontsize=16)

        plt.tight_layout()
        plot_path = output_dir / f"{safe_keyword}_burst_analysis.png"

        # Ensure directory exists before saving (Windows compatibility)
        plot_path.parent.mkdir(parents=True, exist_ok=True)

        plt.savefig(plot_path, bbox_inches="tight", dpi=300)
        plt.close()

        logger.debug(f"Saved burst visualization to {plot_path}")
