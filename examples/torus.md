\begin{tikzpicture}[scale=2.4]
\pgfplotsset{compat=1.16}
\begin{axis}[view={40}{40},width=11cm,height=8cm,axis equal image,hide axis,
title={Parametric torus},title style={font=\tiny\sffamily,yshift=-0.8cm},
colormap={mlblue}{rgb255(0cm)=(24,49,88);rgb255(1cm)=(32,110,183);rgb255(2cm)=(68,179,215);rgb255(3cm)=(186,235,241)}]
\addplot3[surf,shader=faceted,faceted color=cyan!25!blue!35, samples=48,samples y=24,domain=0:360,y domain=0:360,z buffer=sort]
({(2+0.6*cos(y))*cos(x)},{(2+0.6*cos(y))*sin(x)},{0.6*sin(y)});
\end{axis}
\end{tikzpicture}
