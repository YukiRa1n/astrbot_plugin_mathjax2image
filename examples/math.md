# 数学 · 谱分解与高斯积分

对称矩阵把线性变换分解为正交方向上的伸缩。

$$
A=Q\Lambda Q^{\mathsf T},\qquad Q^{\mathsf T}Q=I
$$

$$
\underbrace{\begin{pmatrix}2&1&0\\1&2&0\\0&0&4\end{pmatrix}}_{A}
\qquad
\underbrace{\begin{pmatrix}3&0&0\\0&1&0\\0&0&4\end{pmatrix}}_{\Lambda}
$$

## 多元高斯积分

当 $A$ 正定时，配方把带线性项的积分转化为标准高斯积分。

$$
\begin{aligned}
I(A,b)&=\int_{\mathbb R^n}
 e^{-\frac12 x^{\mathsf T}Ax+b^{\mathsf T}x}\,dx\\[6pt]
&=\frac{(2\pi)^{n/2}}{\sqrt{\det A}}
 e^{\frac12 b^{\mathsf T}A^{-1}b}.
\end{aligned}
$$

## 矩阵导数

令 $f(x)=\tfrac12x^{\mathsf T}Ax-b^{\mathsf T}x$，则

$$\nabla_x f=Ax-b,\qquad \nabla_x^2 f=A.$$
