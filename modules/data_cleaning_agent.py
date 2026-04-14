"""DataCleaningAgent module — Intelligent data cleaning for user-uploaded datasets.

Uses LLM to:
1. Analyze uploaded data structure
2. Generate cleaning code
3. Execute cleaning pipeline
4. Output standardized format for downstream modules
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from llm.openai_completion import OpenAICompletion
from modules.base import BaseModule, HardwareSpec, RunContext

logger = logging.getLogger(__name__)


class DataCleaningAgent(BaseModule):
    """Intelligent data cleaning agent for user-uploaded datasets."""

    @property
    def name(self) -> str:
        return "data_cleaning_agent"

    @property
    def version(self) -> str:
        return "0.1.0"

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "required": ["data_file_path"],
            "properties": {
                "data_file_path": {
                    "type": "string",
                    "description": "Path to uploaded data file (CSV, Excel, JSON, Markdown, TXT)",
                },
                "target_output": {
                    "type": "string",
                    "enum": ["papers_csv", "keyword_year_matrix"],
                    "default": "papers_csv",
                    "description": "Target output format",
                },
            },
        }

    def output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "cleaned_data_path": {"type": "string"},
                "cleaning_report_path": {"type": "string"},
                "stats": {
                    "type": "object",
                    "properties": {
                        "original_rows": {"type": "integer"},
                        "cleaned_rows": {"type": "integer"},
                        "columns_detected": {"type": "array"},
                    },
                },
            },
        }

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "llm_model": {
                    "type": "string",
                    "default": "qwen/qwen3.6-plus",
                    "description": "LLM model for code generation",
                },
                "auto_execute": {
                    "type": "boolean",
                    "default": True,
                    "description": "Automatically execute generated cleaning code",
                },
                "show_code": {
                    "type": "boolean",
                    "default": True,
                    "description": "Show generated cleaning code to user",
                },
            },
        }

    def get_hardware_requirements(self, config: dict) -> HardwareSpec:
        return HardwareSpec(
            cpu_cores=1,
            min_memory_gb=1.0,
            recommended_memory_gb=2.0,
            gpu_required=False,
            estimated_runtime_seconds=30,
        )

    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        """Clean user-uploaded data using LLM-generated code."""
        logger.info("Starting data cleaning agent")

        # Load data
        data_path = Path(input_data["data_file_path"])
        if not data_path.exists():
            raise FileNotFoundError(f"Data file not found: {data_path}")

        logger.info(f"Loading data from: {data_path}")

        # Detect file format and load
        if data_path.suffix == ".csv":
            df = pd.read_csv(data_path)
        elif data_path.suffix in [".xlsx", ".xls"]:
            df = pd.read_excel(data_path)
        elif data_path.suffix == ".json":
            df = pd.read_json(data_path)
        elif data_path.suffix == ".md":
            df = self._parse_markdown_file(data_path)
        elif data_path.suffix == ".txt":
            df = self._parse_text_file(data_path)
        else:
            raise ValueError(f"Unsupported file format: {data_path.suffix}")

        logger.info(f"Loaded data with shape: {df.shape}")

        # Analyze data structure
        data_info = self._analyze_data_structure(df)
        logger.info(f"Detected columns: {list(df.columns)}")

        # Generate cleaning code using LLM
        cleaning_code = self._generate_cleaning_code(
            df_info=data_info,
            target_output=input_data.get("target_output", "papers_csv"),
            config=config,
        )

        # Save cleaning code
        output_dir = context.output_dir
        code_path = output_dir / "cleaning_code.py"
        code_path.write_text(cleaning_code, encoding="utf-8")
        logger.info(f"Saved cleaning code to: {code_path}")

        # Execute cleaning code (if auto_execute)
        if config.get("auto_execute", True):
            logger.info("Executing cleaning code...")
            cleaned_df = self._execute_cleaning_code(df, cleaning_code, output_dir)
        else:
            cleaned_df = df

        # Save cleaned data
        cleaned_path = output_dir / "cleaned_data.csv"
        cleaned_df.to_csv(cleaned_path, index=False)
        logger.info(f"Saved cleaned data to: {cleaned_path}")

        # Generate cleaning report
        report = self._generate_cleaning_report(df, cleaned_df, data_info)
        report_path = output_dir / "cleaning_report.txt"
        report_path.write_text(report, encoding="utf-8")
        logger.info(f"Saved cleaning report to: {report_path}")

        stats = {
            "original_rows": len(df),
            "cleaned_rows": len(cleaned_df),
            "columns_detected": list(df.columns),
        }

        return {
            "cleaned_data_path": str(cleaned_path),
            "papers_csv_path": str(cleaned_path),  # Alias for compatibility with downstream modules
            "cleaning_report_path": str(report_path),
            "stats": stats,
        }

    def _analyze_data_structure(self, df: pd.DataFrame) -> dict:
        """Analyze data structure to help LLM understand the dataset."""
        info = {
            "shape": df.shape,
            "columns": list(df.columns),
            "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
            "sample_values": {},
            "null_counts": df.isnull().sum().to_dict(),
            "unique_counts": {col: df[col].nunique() for col in df.columns},
        }

        # Sample values for each column
        for col in df.columns:
            sample = df[col].dropna().head(3).tolist()
            info["sample_values"][col] = sample

        return info

    def _generate_cleaning_code(self, df_info: dict, target_output: str, config: dict) -> str:
        """Use LLM to generate data cleaning code."""
        llm = OpenAICompletion(model=config.get("llm_model", "qwen/qwen3.6-plus"))

        prompt = f"""You are a data cleaning expert. Generate Python code to clean the following dataset.

Dataset Information:
- Shape: {df_info['shape']}
- Columns: {df_info['columns']}
- Data types: {json.dumps(df_info['dtypes'], indent=2)}
- Null counts: {json.dumps(df_info['null_counts'], indent=2)}
- Unique counts: {json.dumps(df_info['unique_counts'], indent=2)}
- Sample values: {json.dumps(df_info['sample_values'], indent=2)}

Target Output: {target_output}

Requirements:
1. If target is 'papers_csv', clean data to have columns: NUM, TIAB, year, title, abstract
2. If target is 'keyword_year_matrix', generate keyword-year frequency matrix
3. Handle missing values appropriately
4. Remove duplicates
5. Standardize formats (e.g., year as integer)
6. Extract or combine text fields as needed

Generate ONLY the Python code (no explanations). Use pandas as pd. Assume the DataFrame variable is named 'df'.
The function should be named 'clean_data(df)' and return the cleaned DataFrame.

Example structure:
```python
import pandas as pd

def clean_data(df):
    # Your cleaning code here
    cleaned_df = df.copy()
    ...
    return cleaned_df
```"""

        messages = [{"role": "user", "content": prompt}]

        try:
            response = llm.completion(messages=messages, temperature=0.3)
            code = response["choices"][0]["message"]["content"]

            # Extract code from markdown if present
            if "```python" in code:
                code = code.split("```python")[1].split("```")[0].strip()
            elif "```" in code:
                code = code.split("```")[1].split("```")[0].strip()

            logger.info("Successfully generated cleaning code using LLM")
            return code

        except Exception as e:
            logger.error(f"Failed to generate cleaning code: {e}")
            # Fallback to basic cleaning
            return self._get_basic_cleaning_code()

    def _get_basic_cleaning_code(self) -> str:
        """Return basic fallback cleaning code."""
        return """
import pandas as pd

def clean_data(df):
    # Basic cleaning fallback
    cleaned_df = df.copy()

    # Remove completely empty rows
    cleaned_df = cleaned_df.dropna(how='all')

    # Remove duplicates
    cleaned_df = cleaned_df.drop_duplicates()

    return cleaned_df
"""

    def _execute_cleaning_code(self, df: pd.DataFrame, code: str, output_dir: Path) -> pd.DataFrame:
        """Execute generated cleaning code on DataFrame."""
        # Create a temporary Python file with the code
        temp_code_path = output_dir / "temp_cleaning.py"
        temp_code_path.write_text(code, encoding="utf-8")

        try:
            # Execute the code in a controlled namespace
            namespace = {"pd": pd, "df": df}
            exec(code, namespace)

            # Call the clean_data function
            if "clean_data" in namespace:
                cleaned_df = namespace["clean_data"](df)
                logger.info("Successfully executed cleaning code")
                return cleaned_df
            else:
                logger.warning("No clean_data function found in generated code")
                return df

        except Exception as e:
            logger.error(f"Failed to execute cleaning code: {e}")
            logger.warning("Returning original DataFrame")
            return df
        finally:
            # Clean up temp file
            if temp_code_path.exists():
                temp_code_path.unlink()

    def _generate_cleaning_report(
        self,
        original_df: pd.DataFrame,
        cleaned_df: pd.DataFrame,
        data_info: dict,
    ) -> str:
        """Generate a human-readable cleaning report."""
        report_lines = [
            "Data Cleaning Report",
            "=" * 60,
            "",
            "Original Data:",
            f"  Shape: {original_df.shape}",
            f"  Columns: {list(original_df.columns)}",
            "",
            "Cleaned Data:",
            f"  Shape: {cleaned_df.shape}",
            f"  Columns: {list(cleaned_df.columns)}",
            "",
            "Changes:",
            f"  Rows removed: {len(original_df) - len(cleaned_df)}",
            f"  Null values before: {original_df.isnull().sum().sum()}",
            f"  Null values after: {cleaned_df.isnull().sum().sum()}",
            "",
            "Column Details:",
        ]

        for col in cleaned_df.columns:
            report_lines.append(f"  {col}:")
            report_lines.append(f"    - Type: {cleaned_df[col].dtype}")
            report_lines.append(f"    - Non-null: {cleaned_df[col].notna().sum()}")
            report_lines.append(f"    - Unique: {cleaned_df[col].nunique()}")

        return "\n".join(report_lines)

    def _parse_markdown_file(self, file_path: Path) -> pd.DataFrame:
        """Parse markdown file into structured DataFrame.

        Supports multiple formats:
        1. Multiple documents separated by horizontal rules (---)
        2. Structured sections with headers (# Title, ## Abstract, etc.)
        3. YAML frontmatter
        """
        content = file_path.read_text(encoding="utf-8")

        # Split by horizontal rules if multiple documents
        if "---" in content:
            documents = content.split("---")
            records = []

            for doc in documents:
                doc = doc.strip()
                if not doc:
                    continue

                record = self._parse_single_document(doc)
                if record:
                    records.append(record)

            if records:
                return pd.DataFrame(records)

        # Single document
        record = self._parse_single_document(content)
        if record:
            return pd.DataFrame([record])

        # Fallback: treat entire content as one text field
        return pd.DataFrame([{"text": content}])

    def _parse_single_document(self, content: str) -> dict | None:
        """Parse a single document (markdown or text) into a record."""
        import re

        record = {}
        lines = content.strip().split("\n")

        # Try to extract structured fields
        current_field = None
        current_content = []

        for line in lines:
            # Check for headers
            header_match = re.match(r"^#+\s+(.+)$", line)
            if header_match:
                # Save previous field
                if current_field and current_content:
                    record[current_field] = " ".join(current_content).strip()
                    current_content = []

                header_text = header_match.group(1).lower()

                # Map headers to fields
                if "title" in header_text:
                    current_field = "title"
                elif "abstract" in header_text:
                    current_field = "abstract"
                elif "year" in header_text or "date" in header_text:
                    current_field = "year"
                elif "keyword" in header_text:
                    current_field = "keywords"
                elif "author" in header_text:
                    current_field = "authors"
                else:
                    current_field = header_text.replace(" ", "_")

            elif current_field:
                current_content.append(line.strip())

        # Save last field
        if current_field and current_content:
            record[current_field] = " ".join(current_content).strip()

        # Try to extract year from content if not found
        if "year" not in record:
            year_match = re.search(r"\b(19|20)\d{2}\b", content)
            if year_match:
                record["year"] = year_match.group(0)

        return record if record else None

    def _parse_text_file(self, file_path: Path) -> pd.DataFrame:
        """Parse plain text file into structured DataFrame.

        Supports multiple formats:
        1. Multiple records separated by blank lines
        2. Key-value pairs (Field: Value)
        3. Raw text (treated as single document)
        """
        content = file_path.read_text(encoding="utf-8")

        # Try key-value format
        records = []
        current_record = {}

        for line in content.split("\n"):
            line = line.strip()

            # Check for key-value pairs
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip()

                # Map common field names
                if key in ["title", "ti", "t"]:
                    current_record["title"] = value
                elif key in ["abstract", "ab", "abs"]:
                    current_record["abstract"] = value
                elif key in ["year", "date", "py"]:
                    current_record["year"] = value
                elif key in ["keywords", "kw"]:
                    current_record["keywords"] = value
                elif key in ["authors", "au"]:
                    current_record["authors"] = value
                else:
                    current_record[key] = value

            # Empty line might separate records
            elif not line and current_record:
                records.append(current_record)
                current_record = {}

        # Save last record
        if current_record:
            records.append(current_record)

        if records:
            return pd.DataFrame(records)

        # Fallback: treat entire content as one text field
        return pd.DataFrame([{"text": content}])

