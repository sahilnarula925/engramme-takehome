# Part 2: Duplicate requests and batch latency

All times below are in milliseconds, measured from the original simultaneous arrival unless explicitly described as a copy's own duration. The GPU takes **1,000 ms for the entire batch**, for both batch sizes. It starts only when every logical request has a CPU result.

The results use analytic distributions, not simulation. Run the calculation and its independent numerical verification with:

```sh
python3 scripts/statistics.py --verify
python3 -m unittest discover -s tests -p test_statistics.py -v
```

The code uses only the Python standard library and does not contact the service or modify the starter kit.

## Level 1: Baseline, 100 users

Let each original preprocessing duration be $X_i\sim\operatorname{Uniform}(50,400)$, independently. Its CDF is

$$
F(t)=P(X_i\le t)=
\begin{cases}
0,&t<50,\\
(t-50)/350,&50\le t\le400,\\
1,&t>400.
\end{cases}
$$

For $n$ requests, CPU batch completion is $M_n=\max(X_1,\ldots,X_n)$. Independence gives

$$
P(M_n\le t)=P(X_1\le t,\ldots,X_n\le t)=F(t)^n.
$$

Total batch latency is $L_n=1000+M_n$, so its CDF is $P(L_n\le\ell)=F(\ell-1000)^n$. Set this equal to $p=0.95$. With $q_n=p^{1/n}$,

$$
Q_{0.95}(L_n)=1050+350q_n.
$$

For **100 users**, CPU batch p95 is **399.820519504 ms** and total batch p95 is **1,399.820519504 ms**.

This is a percentile across repeated batches, not the individual-request p95 or the expected maximum. Taking the maximum is why the relevant individual CDF value is $0.95^{1/100}$ rather than $0.95$.

## Level 2: Duplicate unfinished requests, 100 users

### Completion-time distribution with the later start

Let $Y_i\sim\operatorname{Uniform}(50,400)$ be the independent duration of request $i$'s potential duplicate, measured from its own start. A duplicate is actually launched only when $X_i>200$.

The logical request's completion time is

$$
Z_i=
\begin{cases}
X_i,&X_i\le200,\\
\min(X_i,200+Y_i),&X_i>200.
\end{cases}
$$

Equivalently, we can sample every $Y_i$ hypothetically and write $Z_i=\min(X_i,200+Y_i)$ for all requests. If $X_i\le200$, then $200+Y_i\ge250>X_i$, so this does not change completed requests or imply they receive a duplicate.

For one logical request to remain unfinished at time $t$, both copies must still be unfinished. Thus

$$
P(Z_i>t)=[1-F(t)][1-F(t-200)].
$$

Its CDF is therefore

$$
H(t)=1-[1-F(t)][1-F(t-200)]
=\begin{cases}
0,&t<50,\\
(t-50)/350,&50\le t\le250,\\
1-\dfrac{(400-t)(600-t)}{350^2},&250<t<400,\\
1,&t\ge400.
\end{cases}
$$

The **earliest duplicate completion is 250 ms**, not 200 ms or 50 ms. For example, a duplicate started at 200 ms with a 300 ms own duration finishes at 500 ms and cannot beat any original in this model. The CDF is continuous at 250 ms, where both expressions give $4/7$.

Logical requests remain independent: each uses its own independent pair $(X_i,Y_i)$, and unlimited CPU capacity prevents cross-request interference. For duplicated-strategy total batch latency $L_n^{\rm dup}$,

$$
P(L_n^{\rm dup}\le\ell)=H(\ell-1000)^n.
$$

At p95, $H(t)=q_n$. For both requested batch sizes, $q_n>H(250)=4/7$, so the quadratic branch applies. Define $\delta_n=1-q_n$ and the distance to the upper endpoint $s=400-t$. Then

$$
s(s+200)=122500\delta_n,
\qquad
s=\sqrt{10000+122500\delta_n}-100.
$$

Consequently,

$$
Q_{0.95}(L_n^{\rm dup})
=1400-s
=1500-\sqrt{10000+122500\delta_n}.
$$

### 1. New p95 and improvement

For **100 users**:

- Baseline total batch p95: **1,399.820519504 ms**.
- With duplicates, CPU batch p95: **399.686400855 ms**.
- With duplicates, total batch p95: **1,399.686400855 ms**.
- Absolute total batch p95 improvement: **0.134118650 ms**.
- Relative total batch p95 improvement: **0.009581132%**.

Improvements are calculated before rounding. Both strategies include the same 1,000 ms GPU stage, so the absolute CPU and total-latency improvements are equal.

### 2. Percentage receiving a duplicate

The denominator is **all original requests**:

$$
P(\text{duplicate launched})=P(X>200)
=\frac{400-200}{400-50}=\frac47
=\boxed{57.142857143\%}.
$$

This is an expected fraction, not a guarantee that exactly that fraction is duplicated in every batch.

### 3. Percentage of all originals for which the duplicate wins

A duplicate wins when $200+Y<X$. This condition already implies the original was unfinished at 200 ms. Since $Y\ge50$, a win is possible only for $X>250$:

$$
\begin{aligned}
P(\text{duplicate wins})
&=\int_{200}^{400}\frac1{350}P(Y<x-200)\,dx\\
&=\int_{250}^{400}\frac{x-250}{350^2}\,dx\\
&=\frac{150^2}{2\cdot350^2}=\frac9{98}.
\end{aligned}
$$

The denominator is **all original requests**, including those never duplicated:

$$
\boxed{9.183673469\%}.
$$

Ties have probability zero because the durations are continuous.

### 4. Percentage of duplicated requests for which the duplicate wins

Now the denominator is **only requests that receive a duplicate**:

$$
P(\text{duplicate wins}\mid X>200)
=\frac{9/98}{4/7}=\frac9{56}
=\boxed{16.071428571\%}.
$$

The two win percentages differ because they use different denominators. A duplicate beating its own original also does not imply it determines the batch's completion time; another request may still be slower.

## Level 3: 10,000 users in one batch

### 1. p95 with and without duplicates

Use the same distributions with $n=10000$, while keeping the GPU stage at exactly 1,000 ms:

- Baseline CPU batch p95: **399.998204739 ms**.
- Baseline total batch p95: **1,399.998204739 ms**.
- With duplicates, CPU batch p95: **399.996858343 ms**.
- With duplicates, total batch p95: **1,399.996858343 ms**.
- Absolute total batch p95 improvement: **0.001346396 ms** (about **1.346396 microseconds**).
- Relative total batch p95 improvement: **0.000096171%**.

### 2. Do the three percentages change?

**No.** Each is a probability for a single original/duplicate pair. Batch size changes neither its duration distributions nor the 200 ms launch rule:

- **57.142857143% of all originals** receive a duplicate.
- **9.183673469% of all originals** have their duplicate finish first.
- **16.071428571% of duplicated requests** have their duplicate finish first.

Expected counts scale with batch size, but these probabilities do not. A larger batch makes the observed fractions more concentrated around those probabilities.

### 3. Effect of batch size and implementation decision

Both one-request completion distributions still have upper endpoint 400 ms. Duplication helps many individual requests, but there remains positive probability of an original finishing arbitrarily close to 400 ms without its later duplicate beating it. Taking the maximum over more users pushes both strategies closer to that same endpoint. Hence both total batch p95 values approach 1,400 ms, and their absolute difference shrinks.

More precisely, as $n$ grows,

$$
\delta_n=1-0.95^{1/n}\sim\frac{-\ln(0.95)}n.
$$

The baseline distance below 1,400 ms is $350\delta_n$. Expanding the duplicate-strategy root gives

$$
s=\sqrt{10000+122500\delta_n}-100
=612.5\delta_n+O(\delta_n^2).
$$

Therefore the improvement is

$$
s-350\delta_n
=262.5\delta_n+O(\delta_n^2)
\sim\frac{262.5[-\ln(0.95)]}n.
$$

This explains why increasing the batch from 100 to 10,000 reduces the absolute improvement by approximately a factor of 100.

**Recommendation: I would not implement this strategy for the stated batch-p95 objective.** It launches an expected **57.142857143% more CPU requests** while saving only about 0.134119 ms for 100 users or 0.001346 ms for 10,000 users. The percentages alone overstate the batch benefit: most launched duplicates lose, and a winning duplicate may not be the batch's straggler.

Under the exercise's assumptions, duplication cannot slow a logical request: $Z_i\le X_i$ on every outcome. If latency were literally the only objective and extra work and implementation complexity had zero cost, enabling it would be mathematically preferable. The engineering recommendation weighs the tiny batch benefit against the additional request volume and complexity. The assumptions remove CPU contention and overhead; I do not charge either against the numerical results above. In a real deployment, I would measure capacity, duration correlation, cancellation behavior, and the actual latency target before considering hedging. Request counts alone do not specify added CPU time because the exercise does not specify whether losing copies are canceled.

## Numerical precision and verification

`scripts/statistics.py` evaluates the closed forms with **80 significant decimal digits**, then rounds displayed results to **nine decimal places**. It rationalizes the upper-tail root as

$$
s=\frac{122500\delta_n}{\sqrt{10000+122500\delta_n}+100},
$$

avoiding subtraction of nearly equal square roots. Python `Decimal` is constructed from exact decimal strings, not binary floating-point literals.

`--verify` independently bisects the batch CDF 240 times on $[1050,1400]$ and checks agreement with each requested analytic p95 within $10^{-60}$ ms. The ideal-arithmetic bisection interval has width $350/2^{240}<2\times10^{-70}$ ms; the looser verification tolerance accommodates decimal arithmetic. It also substitutes each answer into its batch CDF and checks that the result is 0.95 within $10^{-60}$.

The accompanying tests check the delayed-start boundaries, continuity and support, the fixed GPU shift, both quantile branches, stochastic improvement, the large-batch approximation, and the win probabilities via an independent exact triangular-area calculation using rational arithmetic. Decimal comparisons allow for final-digit rounding. **No simulation is used, so there is no sampling error or simulation confidence interval to report.**
