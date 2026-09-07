\begin{tikzpicture}
\pgfplotsset{compat=1.16}
\begin{axis}[view={38}{42},width=12cm,height=8.5cm,xmin=-3,xmax=3,ymin=-3,ymax=3,
title={Non-convex loss landscape},title style={font=\large\sffamily},
xlabel={$w_1$},ylabel={$w_2$},zlabel={$\mathcal L$},
grid=major,major grid style={draw=gray!18},tick style={draw=gray!45},
label style={font=\small},ticklabel style={font=\small},
colormap={mlblue}{rgb255(0cm)=(24,49,88);rgb255(1cm)=(32,110,183);rgb255(2cm)=(68,179,215);rgb255(3cm)=(186,235,241)}]
\addplot3[surf,shader=flat,samples=48,domain=-3:3,y domain=-3:3]
{0.12*(x^2+y^2)+0.55*sin(deg(2*x))*cos(deg(2*y))+0.25*sin(deg(3*x+1.5*y))+1};
\end{axis}
\end{tikzpicture}
