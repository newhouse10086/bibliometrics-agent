"""Internationalization (i18n) support for web interface.

Provides translations for Chinese (zh) and English (en).
"""

from __future__ import annotations

TRANSLATIONS = {
    "en": {
        # Common
        "app_title": "Bibliometrics Agent",
        "app_subtitle": "LLM-Powered Bibliometric Analysis System",
        "language": "Language",

        # Navigation
        "home": "Home",
        "upload": "Upload Data",
        "analyze": "Analyze",

        # Index page
        "hero_title": "Automated Bibliometric Analysis",
        "hero_description": "Analyze scientific literature using machine learning and topic modeling. Upload your data or start from a research domain.",
        "card_automated_title": "Research Domain Analysis",
        "card_automated_desc": "Start from a research topic and automatically fetch papers from PubMed/BioRxiv",
        "card_upload_title": "Upload Custom Data",
        "card_upload_desc": "Upload your own dataset (CSV, Excel, JSON, Markdown, or TXT)",
        "domain_label": "Research Domain",
        "domain_placeholder": "e.g., machine learning, CRISPR, climate change",
        "max_papers": "Maximum Papers",
        "topic_range": "Topic Range",
        "start_analysis": "Start Analysis",
        "upload_file": "Upload File",
        "supported_formats": "Supported formats: CSV, Excel, JSON, Markdown, TXT",

        # Run page
        "run_title": "Configure Analysis",
        "run_config": "Configuration",
        "run_settings": "Analysis Settings",
        "run_start": "Start Pipeline",

        # Results page
        "results_title": "Analysis Results",
        "results_overview": "Overview",
        "results_topics": "Topics",
        "results_visualizations": "Visualizations",
        "download_results": "Download Results",

        # Status messages
        "processing": "Processing...",
        "success": "Success!",
        "error": "Error",
        "loading": "Loading...",

        # Features
        "feature_1_title": "Topic Modeling",
        "feature_1_desc": "Discover hidden themes using LDA",
        "feature_2_title": "Burst Detection",
        "feature_2_desc": "Identify trending keywords over time",
        "feature_3_title": "Network Analysis",
        "feature_3_desc": "Visualize citation and keyword networks",

        # Footer
        "footer_text": "Powered by LLM and Machine Learning",

        # Workspace
        "workspace_title": "Workspace Management",
        "workspace_select": "Select Workspace",
        "workspace_create": "Create New Workspace",
        "workspace_name": "Workspace Name",
        "workspace_description": "Description",
        "workspace_created": "Created",
        "workspace_status": "Status",
        "workspace_files": "Files",
        "workspace_size": "Size",
        "workspace_actions": "Actions",
        "workspace_delete": "Delete",
        "workspace_open": "Open",
        "workspace_no_workspaces": "No workspaces yet. Create one to get started!",
        "workspace_default": "Default Workspace",
    },
    "zh": {
        # Common
        "app_title": "文献计量分析智能体",
        "app_subtitle": "基于大语言模型的文献计量分析系统",
        "language": "语言",

        # Navigation
        "home": "首页",
        "upload": "上传数据",
        "analyze": "分析",

        # Index page
        "hero_title": "自动化文献计量分析",
        "hero_description": "使用机器学习和主题建模分析科学文献。上传您的数据或从研究领域开始。",
        "card_automated_title": "研究领域分析",
        "card_automated_desc": "从研究主题开始，自动从PubMed/BioRxiv获取论文",
        "card_upload_title": "上传自定义数据",
        "card_upload_desc": "上传您自己的数据集（CSV、Excel、JSON、Markdown或TXT）",
        "domain_label": "研究领域",
        "domain_placeholder": "例如：机器学习、CRISPR、气候变化",
        "max_papers": "最大论文数",
        "topic_range": "主题范围",
        "start_analysis": "开始分析",
        "upload_file": "上传文件",
        "supported_formats": "支持格式：CSV、Excel、JSON、Markdown、TXT",

        # Run page
        "run_title": "配置分析",
        "run_config": "配置",
        "run_settings": "分析设置",
        "run_start": "启动流水线",

        # Results page
        "results_title": "分析结果",
        "results_overview": "概览",
        "results_topics": "主题",
        "results_visualizations": "可视化",
        "download_results": "下载结果",

        # Status messages
        "processing": "处理中...",
        "success": "成功！",
        "error": "错误",
        "loading": "加载中...",

        # Features
        "feature_1_title": "主题建模",
        "feature_1_desc": "使用LDA发现隐藏主题",
        "feature_2_title": "突发检测",
        "feature_2_desc": "识别随时间趋势变化的关键词",
        "feature_3_title": "网络分析",
        "feature_3_desc": "可视化引用和关键词网络",

        # Footer
        "footer_text": "由大语言模型和机器学习驱动",

        # Workspace
        "workspace_title": "工作区管理",
        "workspace_select": "选择工作区",
        "workspace_create": "创建新工作区",
        "workspace_name": "工作区名称",
        "workspace_description": "描述",
        "workspace_created": "创建时间",
        "workspace_status": "状态",
        "workspace_files": "文件数",
        "workspace_size": "大小",
        "workspace_actions": "操作",
        "workspace_delete": "删除",
        "workspace_open": "打开",
        "workspace_no_workspaces": "暂无工作区，创建一个开始使用！",
        "workspace_default": "默认工作区",
    },
}


def get_translations(lang: str = "en") -> dict:
    """Get translations for specified language.

    Args:
        lang: Language code ("en" or "zh")

    Returns:
        Dictionary of translations
    """
    return TRANSLATIONS.get(lang, TRANSLATIONS["en"])


def get_available_languages() -> list[str]:
    """Get list of supported languages."""
    return list(TRANSLATIONS.keys())
