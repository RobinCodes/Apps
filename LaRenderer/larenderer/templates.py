"""Starting documents, and the snippets on the Insert menu.

%%CURSOR%% is where the caret lands after inserting. %%SEL%% is replaced by
whatever was selected, so selecting a word and inserting Bold wraps it.
"""

from __future__ import annotations

CURSOR = "%%CURSOR%%"
SELECTION = "%%SEL%%"


ARTICLE = r"""\documentclass[11pt,a4paper]{article}
\usepackage[margin=1in]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage[colorlinks=true,linkcolor=blue,urlcolor=magenta]{hyperref}
\usepackage{microtype}

\title{%%CURSOR%%Untitled}
\author{Robin}
\date{\today}

\begin{document}
\maketitle

\section{Introduction}

Write here.

\end{document}
"""

REPORT = r"""\documentclass[11pt,a4paper]{report}
\usepackage[margin=1in]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage[colorlinks=true,linkcolor=blue,urlcolor=magenta]{hyperref}
\usepackage{microtype}

\title{%%CURSOR%%Untitled Report}
\author{Robin}
\date{\today}

\begin{document}
\maketitle
\tableofcontents

\chapter{Introduction}

Write here.

\end{document}
"""

MATHS = r"""\documentclass[11pt,a4paper]{article}
\usepackage[margin=1in]{geometry}
\usepackage{amsmath,amssymb,amsthm}
\usepackage[colorlinks=true,linkcolor=blue]{hyperref}
\usepackage{microtype}

\newtheorem{theorem}{Theorem}[section]
\newtheorem{lemma}[theorem]{Lemma}
\theoremstyle{definition}
\newtheorem{definition}[theorem]{Definition}

\title{%%CURSOR%%Notes}
\author{Robin}
\date{\today}

\begin{document}
\maketitle

\section{Preliminaries}

\begin{definition}
    A set is countable if there is an injection into $\mathbb{N}$.
\end{definition}

\begin{theorem}[Gauss]
    \[ \sum_{k=1}^{n} k = \frac{n(n+1)}{2} \]
\end{theorem}

\begin{proof}
    Pair the first and last terms.
\end{proof}

\end{document}
"""

BEAMER = r"""\documentclass[aspectratio=169]{beamer}
% Madrid ships with beamer itself. Nicer themes such as metropolis are a
% separate install: sudo pacman -S texlive-latexextra
\usetheme{Madrid}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}

\title{%%CURSOR%%Untitled Talk}
\author{Robin}
\date{\today}

\begin{document}

\begin{frame}
    \titlepage
\end{frame}

\begin{frame}{First slide}
    \begin{itemize}
        \item A point
        \item Another point
    \end{itemize}
\end{frame}

\end{document}
"""

LETTER = r"""\documentclass[11pt,a4paper]{letter}
\usepackage[margin=1in]{geometry}

\signature{Robin}
\address{Your street \\ Your town \\ Your postcode}

\begin{document}

\begin{letter}{%%CURSOR%%Recipient \\ Their street \\ Their town}

\opening{Dear Sir or Madam,}

Write here.

\closing{Yours faithfully,}

\end{letter}
\end{document}
"""

TEMPLATES = [
    ("Article", "A one-column article — the usual starting point", ARTICLE),
    ("Report", "Chapters and a table of contents", REPORT),
    ("Maths notes", "Theorem, lemma and proof environments set up", MATHS),
    ("Presentation", "Beamer slides", BEAMER),
    ("Letter", "A formal letter", LETTER),
]


SNIPPETS = [
    ("Structure", [
        ("Section", "\\section{" + CURSOR + SELECTION + "}\n"),
        ("Subsection", "\\subsection{" + CURSOR + SELECTION + "}\n"),
        ("Paragraph break", "\n\n" + CURSOR),
    ]),
    ("Text", [
        ("Bold", "\\textbf{" + SELECTION + CURSOR + "}"),
        ("Italic", "\\emph{" + SELECTION + CURSOR + "}"),
        ("Monospace", "\\texttt{" + SELECTION + CURSOR + "}"),
        ("Footnote", "\\footnote{" + SELECTION + CURSOR + "}"),
        ("Quote", "\\begin{quote}\n  " + SELECTION + CURSOR + "\n\\end{quote}\n"),
    ]),
    ("Maths", [
        ("Inline maths", "$" + SELECTION + CURSOR + "$"),
        ("Display maths", "\\[\n  " + SELECTION + CURSOR + "\n\\]\n"),
        ("Aligned equations",
         "\\begin{align}\n  " + CURSOR + "a &= b \\\\\n  c &= d\n\\end{align}\n"),
        ("Cases",
         "\\[\n  f(x) = \\begin{cases}\n    " + CURSOR +
         "1 & x > 0 \\\\\n    0 & \\text{otherwise}\n  \\end{cases}\n\\]\n"),
        ("Matrix",
         "\\[\n  \\begin{pmatrix}\n    " + CURSOR +
         "a & b \\\\\n    c & d\n  \\end{pmatrix}\n\\]\n"),
        ("Fraction", "\\frac{" + CURSOR + "}{}"),
        ("Sum", "\\sum_{i=1}^{n} " + CURSOR),
        ("Integral", "\\int_{0}^{\\infty} " + CURSOR + "\\,dx"),
    ]),
    ("Lists", [
        ("Itemize",
         "\\begin{itemize}\n  \\item " + CURSOR + SELECTION + "\n  \\item \n\\end{itemize}\n"),
        ("Enumerate",
         "\\begin{enumerate}\n  \\item " + CURSOR + SELECTION + "\n  \\item \n\\end{enumerate}\n"),
        ("Description",
         "\\begin{description}\n  \\item[Term] " + CURSOR + "\n\\end{description}\n"),
    ]),
    ("Floats", [
        ("Figure",
         "\\begin{figure}[htbp]\n  \\centering\n  \\includegraphics[width=0.8\\linewidth]{"
         + CURSOR + "image}\n  \\caption{Caption}\n  \\label{fig:label}\n\\end{figure}\n"),
        ("Table",
         "\\begin{table}[htbp]\n  \\centering\n  \\begin{tabular}{lcr}\n    \\toprule\n"
         "    Left & Centre & Right \\\\\n    \\midrule\n    " + CURSOR +
         "a & b & c \\\\\n    d & e & f \\\\\n    \\bottomrule\n  \\end{tabular}\n"
         "  \\caption{Caption}\n  \\label{tab:label}\n\\end{table}\n"),
        ("Code listing",
         "\\begin{lstlisting}[language=Python]\n" + CURSOR + SELECTION +
         "\n\\end{lstlisting}\n"),
    ]),
    ("References", [
        ("Label", "\\label{" + CURSOR + "}"),
        ("Reference", "\\ref{" + CURSOR + "}"),
        ("Citation", "\\cite{" + CURSOR + "}"),
        ("URL", "\\url{" + CURSOR + SELECTION + "}"),
    ]),
]
