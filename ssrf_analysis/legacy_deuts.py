import math
import numpy as np

def lorentzian(freq, f0, w, K, theta, c):
    #w = FWHM, always positive, MHz
    #f0 = central frequency
    #K = scaling, neg is flipped
    #c = y intercept
    x = (f0-freq)/(w/2)
    D = K*(x/(1+x**2))
    A = K*(1/(1+x**2))
    R = A*np.cos(theta)-D*np.sin(theta)+c
    # R = A*cos-D*sin
    return R

def lorentz2(freq, f0, gamma, cc, b):
    top = gamma
    bot = (freq-f0)**2 + gamma**2
    out = top/bot
    return cc*out + b

def holeNbump(freq, f0, w, KHB, theta, omegD):
    hole = lorentzian(freq, f0, w, KHB, theta, 0.0)
    bump = lorentzian(freq, 2*omegD - f0, w, -0.5*KHB, theta, 0.0)
    total = hole + bump
    return total

def linear(x, a, b):
    y = a*x + b
    return y

def quadratic(x, a, b, c):
    y = a*x**2 + b*x + c
    return y

def cubic(x, a, b, c, d):
    y = a*x**3 + b*x**2 + c*x + d
    return y

def deut_half(freq, epsi, omegD, omegQ, A, eta, phi):
    #The functional form of the deuteron NMR signal from 'A line-shape analysis for spin-1 NMR signals', Dulya et al.
    #epsi = +/-1
    #phi in radians
    R = (freq - omegD)/(3*omegQ)
    theta = omegQ/omegD
    rhosq = np.sqrt(A**2 + (1 - epsi*R - eta*np.cos(2*phi))**2)
    rho = np.sqrt(rhosq)
    alpha = np.arccos((1 - epsi*R - eta*np.cos(2*phi))/rhosq)
    Y = np.sqrt(3 - eta*np.cos(2*phi))
    left = 2*np.cos(alpha/2)*(np.arctan((Y**2 - rhosq)/(2*Y*rho*np.sin(alpha/2))) + math.pi/2)
    right_num = Y**2 + rhosq + 2*Y*rho*np.cos(alpha/2)
    right_dem = Y**2 + rhosq - 2*Y*rho*np.cos(alpha/2)
    right = np.sin(alpha/2)*np.log(right_num/right_dem)
    unpol = (1/(2*math.pi*rho))*(left + right)  #equation 14 in Dulya
    return unpol

def deut_phiavg_bare(freq, epsi, omegD, omegQ, A, eta):
    #averaging the function over phi from 0 to 2pi
    R = (freq - omegD)/(3*omegQ)
    theta = omegQ/omegD
    J = 64
    avgtot = 0.0
    for j in range(J):
        phi = 2*math.pi*j/J
        unavg_num = np.sqrt(3)*deut_half(freq, epsi, omegD, omegQ, A, eta, phi)
        unavg_den = np.sqrt(3 - eta*np.cos(2*phi))
        avgtot += unavg_num/unavg_den
    aver = avgtot/(J+1)
    return aver

def deut_phiavg(freq, epsi, omegD, omegQ, A, eta, r):
    #averaging the function over phi from 0 to 2pi
    R = (freq - omegD)/(3*omegQ)
    theta = omegQ/omegD
    J = 64
    avgtot = 0.0
    for j in range(J):
        phi = 2*math.pi*j/J
        unavg_num = np.sqrt(3)*deut_half(freq, epsi, omegD, omegQ, A, eta, phi)
        unavg_den = np.sqrt(3 - eta*np.cos(2*phi))
        avgtot += unavg_num/unavg_den
    aver = avgtot/(J+1)
    if epsi == -1:
        aver = aver*((r**(1+3*theta*R) - 1)/(r**(1+theta*R)))
    if epsi == 1:
        aver = aver*((r**2 - r**(1-3*theta*r))/(r**(1-theta*R)))
    abso = aver/omegQ
    return abso

def deut_phiavg_rev(freq, epsi, omegD, omegQ, A, eta, r):
    #averaging the function over phi from 0 to 2pi
    R = (freq - omegD)/(3*omegQ)
    theta = omegQ/omegD
    J = 64
    avgtot = 0.0
    for j in range(J):
        phi = 2*math.pi*j/J
        unavg_num = np.sqrt(3)*deut_half(freq, epsi, omegD, omegQ, A, eta, phi)
        unavg_den = np.sqrt(3 - eta*np.cos(2*phi))
        avgtot += unavg_num/unavg_den
    aver = avgtot/(J+1)
    if epsi == 1:
        aver = aver*((r**(1+3*theta*R) - 1)/(r**(1+theta*R)))
    if epsi == -1:
        aver = aver*((r**2 - r**(1-3*theta*r))/(r**(1-theta*R)))
    abso = aver/omegQ
    return abso

def deut_phiavg_NonBolt(freq, epsi, omegD, omegQ, A, eta, r):
    #averaging the function over phi from 0 to 2pi
    R = (freq - omegD)/(3*omegQ)
    theta = omegQ/omegD
    J = 64
    avgtot = 0.0
    for j in range(J):
        phi = 2*math.pi*j/J
        unavg_num = np.sqrt(3)*deut_half(freq, epsi, omegD, omegQ, A, eta, phi)
        unavg_den = np.sqrt(3 - eta*np.cos(2*phi))
        avgtot += unavg_num/unavg_den
    aver = r*avgtot/(J+1)
    abso = aver/omegQ
    return abso

def deut_phiavg_HB(freq, epsi, omegD, omegQ, A, eta, r, f0, w, KHB1, KHB2):
    base = deut_phiavg(freq, epsi, omegD, omegQ, A, eta, r)
    hole = lorentzian(freq, f0, w, KHB1, 0.0, 0.0)
    bump = lorentzian(freq, 2*omegD - f0, w, KHB2, 0.0, 0.0)
    if epsi == 1:
        base = base - hole + bump
    if epsi == -1:
        base = base - hole + bump
    return base

def holebump(freq, omegD, f0, w, KHB1, KHB2):
    hole = lorentzian(freq, f0, w, KHB1, 0.0, 0.0)
    bump = lorentzian(freq, 2*omegD - f0, w, KHB2, 0.0, 0.0)
    total = bump - hole
    return total

def DHB_PHI_HC(freq, mol, epsi, r, f0, w, Ka, Kb):
    omegD = 32.770224814682045
    omegQ1 = 0.021716202893838885
    omegQ2 = 0.026175071963974693
    A1 = 0.030764636726304282
    eta1 = 0.06022708879490207
    eta2 = 0.209831140974086
    K = 0.2380571648521008
    scale = 0.0005240312423109996
    out = 0.0
    if epsi == -1 and mol == 1:
        out = scale*(1-K)*deut_phiavg_HB(freq, -1, omegD, omegQ1, A1, eta1, r, f0, w, Ka, 0.5*Kb)
    if epsi == 1 and mol == 1:
        out = scale*(1-K)*deut_phiavg_HB(freq, 1, omegD, omegQ1, A1, eta1, r, f0, w, Kb, 0.5*Ka)
    if epsi == -1 and mol == 0:
        out = scale*K*deut_phiavg_HB(freq, -1, omegD, omegQ2, A1, eta2, r, f0, w, Ka, 0.5*Kb)
    if epsi == 1 and mol == 0:
        out = scale*K*deut_phiavg_HB(freq, 1, omegD, omegQ2, A1, eta2, r, f0, w, Kb, 0.5*Ka)
    return out

def deut_fit(freq, omegD, omegQ, A, eta, r):
    #both functions added together
    neg = deut_phiavg(freq, -1, omegD, omegQ, A, eta, r)
    pos = deut_phiavg(freq, 1, omegD, omegQ, A, eta, r)
    total = neg + pos    #equation 24 in Dulya
    return total

def deut_fit_rev(freq, omegD, omegQ, A, eta, r):
    #both functions added together
    neg = deut_phiavg_rev(freq, -1, omegD, omegQ, A, eta, r)
    pos = deut_phiavg_rev(freq, 1, omegD, omegQ, A, eta, r)
    total = neg + pos    #equation 24 in Dulya
    return total

def deut_fit_NonBolt(freq, omegD, omegQ, A, eta, rN, rP):
    #both functions added together
    neg = deut_phiavg_NonBolt(freq, -1, omegD, omegQ, A, eta, rN)
    pos = deut_phiavg_NonBolt(freq, 1, omegD, omegQ, A, eta, rP)
    total = neg + pos    #equation 24 in Dulya
    return total

def deut_fit_flippy(freq, omegD, omegQ, A, eta, r):
    #both functions added together
    neg = deut_phiavg(freq, -1, omegD, omegQ, A, eta, r)
    pos = deut_phiavg(freq, 1, omegD, omegQ, A, eta, r)
    total = neg - pos    #equation 24 in Dulya
    return total

def deut_fit_HB(freq, omegD, omegQ, A, eta, r, f0, w, Ka, Kb):
    neg = deut_phiavg_HB(freq, -1, omegD, omegQ, A, eta, r, f0, w, Ka, 0.5*Kb)
    pos = deut_phiavg_HB(freq, 1, omegD, omegQ, A, eta, r, f0, w, Kb, 0.5*Ka)
    total = neg + pos
    return total

def deut_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale):
    #carbon and oxygen bonds separately
    A2 = A1*omegQ1/omegQ2
    one = deut_fit(freq, omegD, omegQ1, A1, eta1, r)
    two = deut_fit(freq, omegD, omegQ2, A2, eta2, r)
    total = scale*((1-K)*one + K*two)
    return total

def deut_double_NonBolt(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, rN, rP, K, scale):
    #carbon and oxygen bonds separately
    A2 = A1*omegQ1/omegQ2
    one = deut_fit_NonBolt(freq, omegD, omegQ1, A1, eta1, rN, rP)
    two = deut_fit_NonBolt(freq, omegD, omegQ2, A2, eta2, rN, rP)
    total = scale*((1-K)*one + K*two)
    return total

def deut_double_rev(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale):
    #carbon and oxygen bonds separately
    A2 = A1*omegQ1/omegQ2
    one = deut_fit_rev(freq, omegD, omegQ1, A1, eta1, r)
    two = deut_fit_rev(freq, omegD, omegQ2, A2, eta2, r)
    total = scale*((1-K)*one + K*two)
    return total

def deut_double_Q(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale):
    PzN = r*r - 1
    PzD = r*r + r + 1
    Pz = PzN/PzD
    a = -0.38063
    b = -0.61489
    Q = a*Pz**4 + b*Pz**2 + 1
    total = Q*deut_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale)
    return total

def FlexQ(r):
    omegQ1 = 2.17286634e-02
    PzN = r*r - 1
    PzD = r*r + r + 1
    Pz = PzN/PzD
    FlexQ = Pz*(-1*Pz + 0.5*omegQ1) + 1
    #FlexQ = Pz*(-0.9076*Pz + 0.005336) + 1
    return FlexQ

def deut_double_cube(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, x3, x2, x1, x0):
    recur = deut_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale)
    cube = cubic(freq, x3, x2, x1, x0)
    return recur + cube

def holeNbump(freq, omegD, f0, w, KHB):
    hole = lorentzian(freq, f0, w, KHB, 0.0, 0.0)
    bump = lorentzian(freq, 2*omegD - f0, w, 0.5*KHB, 0.0, 0.0)
    total = bump - hole
    return total

def holeNbump2(freq, omegD, f0, w, Ka, Kb):
    hole = lorentzian(freq, f0, w, Ka, 0.0, 0.0)
    bump = lorentzian(freq, 2*omegD - f0, w, Kb, 0.0, 0.0)
    total = bump - hole
    return total

def DD_HB_slap(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, f1, f2, w1, w2, K1, K2):
    base = deut_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale)
    HNB1 = scale*holeNbump(freq,omegD,f1,w1,K1)
    HNB2 = scale*holeNbump(freq,omegD,f2,w2,K2)#assume same width
    total = base + HNB1 + HNB2
    return total

def DD_HB_SHC(freq, r, f1, f2, w1, w2, K1, K2):
    omegD = 32.770224814682045
    omegQ1 = 0.021716202893838885
    omegQ2 = 0.026175071963974693
    A1 = 0.030764636726304282
    eta1 = 0.06022708879490207
    eta2 = 0.209831140974086
    K = 0.2380571648521008
    scale = 0.0005240312423109996
    out = DD_HB_slap(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, f1, f2, w1, w2, K1, K2)
    return out

def DD_hardcode(freq, r): 
    omegD = 32.20594369869279
    omegQ1 = 0.02163207128968159
    omegQ2 = 0.02991592705730971
    A1 = 0.0342369160955703
    eta1 = 0.05178222557664749
    eta2 = 0.0820979551482718
    K = 0.11649954602872603
    scale = 0.0022979193725171823
    out = deut_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale)
    return out

def DD_HC_OpenD(freq, omegD, r): 
    omegQ1 = 0.02443932960112754
    omegQ2 = 0.029250945928615883
    A1 = 0.030570759056438798
    eta1 = 0.06075110520772423
    eta2 = 0.20093007172833274
    K = 0.21149454224770958
    scale = 0.00032487345135919014
    out = deut_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale)
    return out 

def deut_double_HB(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, f0, w, Ka, Kb):
    A2 = A1*omegQ1/omegQ2
    one = deut_fit_HB(freq, omegD, omegQ1, A1, eta1, r, f0, w, Ka, Kb)
    two = deut_fit_HB(freq, omegD, omegQ2, A2, eta2, r, f0, w, Ka, Kb)
    total = scale*((1-K)*one + K*two)
    return total

def deut_double_HB_cube(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, f0, w, Ka, Kb, x3, x2, x1, x0):
    A2 = A1*omegQ1/omegQ2
    one = deut_fit_HB(freq, omegD, omegQ1, A1, eta1, r, f0, w, Ka, Kb)
    two = deut_fit_HB(freq, omegD, omegQ2, A2, eta2, r, f0, w, Ka, Kb)
    total = scale*((1-K)*one + K*two)
    cube = cubic(freq, x3, x2, x1, x0)
    return total + cube

def deut_double_HB_just(freq, omegD, scale, f0, w, Ka, Kb):
    one = holebump(freq, omegD, f0, w, Ka, 0.5*Kb)
    two = holebump(freq, omegD, f0, w, Kb, 0.5*Ka)
    total = scale*(one + two)
    return total

def DHB_hardcode(freq, r, f0, w, Ka, Kb):
    omegD = 32.770224814682045
    omegQ1 = 0.021716202893838885
    omegQ2 = 0.026175071963974693
    A1 = 0.030764636726304282
    eta1 = 0.06022708879490207
    eta2 = 0.209831140974086
    K = 0.2380571648521008
    scale = 0.0005240312423109996
    out = deut_double_HB(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, f0, w, Ka, Kb)
    return out      
                   
def deut_double_NaiveHB(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, f0, w, KHB, theta):
    base = deut_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale)
    hnb = holeNbump(freq, f0, w, KHB, theta, omegD)
    total = base + hnb
    return total

def deut_disp_half(freq, epsi, omegD, omegQ, A, eta, phi):
    #The functional form of the deuteron NMR signal from 'A line-shape analysis for spin-1 NMR signals', Dulya et al.
    #epsi = +/-1
    #phi in radians
    R = (freq - omegD)/(3*omegQ)
    theta = omegQ/omegD
    rhosq = np.sqrt(A**2 + (1 - epsi*R - eta*np.cos(2*phi))**2)
    rho = np.sqrt(rhosq)
    alpha = np.arccos((1 - epsi*R - eta*np.cos(2*phi))/rhosq)
    Y = np.sqrt(3 - eta*np.cos(2*phi))
    left = 2*np.sin(alpha/2)*(np.arctan((Y**2 - rhosq)/(2*Y*rho*np.sin(alpha/2))) + math.pi/2)
    right_num = Y**2 + rhosq + 2*Y*rho*np.cos(alpha/2)
    right_dem = Y**2 + rhosq - 2*Y*rho*np.cos(alpha/2)
    right = np.cos(alpha/2)*(np.log(right_dem/right_num))
    unpol = 1*(1/(2*math.pi*rho))*(left + right)  #CHANGE
    return unpol

def deut_disp_phiavg(freq, epsi, omegD, omegQ, A, eta, r):
    #averaging the function over phi from 0 to 2pi
    R = (freq - omegD)/(3*omegQ)
    theta = omegQ/omegD
    J = 64
    avgtot = 0.0
    for j in range(J):
        phi = 2*math.pi*j/J
        unavg_num = np.sqrt(3)*deut_disp_half(freq, epsi, omegD, omegQ, A, eta, phi)
        unavg_den = np.sqrt(3 - eta*np.cos(2*phi))
        avgtot += unavg_num/unavg_den
    aver = avgtot/(J+1)
    if epsi == -1:
        aver = 1*aver*((r**(1+3*theta*R) - 1)/(r**(1+theta*R)))
    if epsi == 1:
        aver = -1*aver*((r**2 - r**(1-3*theta*r))/(r**(1-theta*R)))
    abso = aver/omegQ
    return abso

def deut_disp_phiavg_rev(freq, epsi, omegD, omegQ, A, eta, r):
    #averaging the function over phi from 0 to 2pi
    R = (freq - omegD)/(3*omegQ)
    theta = omegQ/omegD
    J = 64
    avgtot = 0.0
    for j in range(J):
        phi = 2*math.pi*j/J
        unavg_num = np.sqrt(3)*deut_disp_half(freq, epsi, omegD, omegQ, A, eta, phi)
        unavg_den = np.sqrt(3 - eta*np.cos(2*phi))
        avgtot += unavg_num/unavg_den
    aver = avgtot/(J+1)
    if epsi == 1:
        aver = -1*aver*((r**(1+3*theta*R) - 1)/(r**(1+theta*R)))
    if epsi == -1:
        aver = 1*aver*((r**2 - r**(1-3*theta*r))/(r**(1-theta*R)))
    abso = aver/omegQ
    return abso

def deut_disp_phiavg_NonBolt(freq, epsi, omegD, omegQ, A, eta, r):
    #averaging the function over phi from 0 to 2pi
    R = (freq - omegD)/(3*omegQ)
    theta = omegQ/omegD
    J = 64
    avgtot = 0.0
    for j in range(J):
        phi = 2*math.pi*j/J
        unavg_num = np.sqrt(3)*deut_disp_half(freq, epsi, omegD, omegQ, A, eta, phi)
        unavg_den = np.sqrt(3 - eta*np.cos(2*phi))
        avgtot += unavg_num/unavg_den
    aver = r*(avgtot/(J+1))
    abso = aver/omegQ
    return abso

def deut_disp_fit(freq, omegD, omegQ, A, eta, r):
    #both functions added together
    neg = deut_disp_phiavg(freq, -1, omegD, omegQ, A, eta, r)
    pos = deut_disp_phiavg(freq, 1, omegD, omegQ, A, eta, r)
    total = neg + pos    #equation 24 in Dulya
    return total

def deut_disp_fit_flippy(freq, omegD, omegQ, A, eta, r):
    #both functions added together
    neg = deut_disp_phiavg(freq, -1, omegD, omegQ, A, eta, r)
    pos = deut_disp_phiavg(freq, 1, omegD, omegQ, A, eta, r)
    total = neg - pos    #equation 24 in Dulya
    return total

def deut_disp_fit_rev(freq, omegD, omegQ, A, eta, r):
    #both functions added together
    neg = deut_disp_phiavg_rev(freq, -1, omegD, omegQ, A, eta, r)
    pos = deut_disp_phiavg_rev(freq, 1, omegD, omegQ, A, eta, r)
    total = neg + pos    #equation 24 in Dulya
    return total

def deut_disp_fit_NonBolt(freq, omegD, omegQ, A, eta, rN, rP):
    #both functions added together
    neg = deut_disp_phiavg_NonBolt(freq, -1, omegD, omegQ, A, eta, rN)
    pos = deut_disp_phiavg_NonBolt(freq, 1, omegD, omegQ, A, eta, rP)
    total = neg + pos    #equation 24 in Dulya
    return total

def deut_disp_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale):
    #carbon and oxygen bonds separately
    A2 = A1*omegQ1/omegQ2
    one = deut_disp_fit(freq, omegD, omegQ1, A1, eta1, r)
    two = deut_disp_fit(freq, omegD, omegQ2, A2, eta2, r)
    total = scale*((1-K)*one + K*two)
    return total

def deut_disp_double_rev(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale):
    #carbon and oxygen bonds separately
    A2 = A1*omegQ1/omegQ2
    one = deut_disp_fit_rev(freq, omegD, omegQ1, A1, eta1, r)
    two = deut_disp_fit_rev(freq, omegD, omegQ2, A2, eta2, r)
    total = scale*((1-K)*one + K*two)
    return total

def deut_disp_double_NonBolt(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, rN, rP, K, scale):
    #carbon and oxygen bonds separately
    A2 = A1*omegQ1/omegQ2
    one = deut_disp_fit_NonBolt(freq, omegD, omegQ1, A1, eta1, rN, rP)
    two = deut_disp_fit_NonBolt(freq, omegD, omegQ2, A2, eta2, rN, rP)
    total = scale*((1-K)*one + K*two)
    return total

def deut_real_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase):
    absor = deut_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale)
    disper = deut_disp_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale)
    real = absor*np.cos(phase) + disper*np.sin(phase)
    return real

def deut_real_double_rev(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase):
    absor = deut_double_rev(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale)
    disper = deut_disp_double_rev(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale)
    real = absor*np.cos(phase) + disper*np.sin(phase)
    return real

def Xi(r):
    PzN = r*r - 1
    PzD = r*r + r + 1
    Pz = PzN/PzD
    a = -0.38063
    b = -0.61489
    Ksi = a*Pz**4 + b*Pz**2 + 1
    return Ksi

def deut_real_Xi(freq,omegD,omegQ,A,eta,r,scale,phase):
    absor = deut_fit(freq, omegD, omegQ, A, eta, r)
    disper = deut_disp_fit(freq, omegD, omegQ, A, eta, r)
    real = absor*np.cos(phase) + disper*np.sin(phase)
    real2 = scale*real
    Ksi = Xi(r)
    real3 = Ksi*real2
    return real3

def deut_real_Xi_flippy(freq,omegD,omegQ,A,eta,r,scale,phase):
    absor = deut_fit_flippy(freq, omegD, omegQ, A, eta, r)
    disper = deut_disp_fit_flippy(freq, omegD, omegQ, A, eta, r)
    real = absor*np.cos(phase) + disper*np.sin(phase)
    real2 = scale*real
    Ksi = Xi(r)
    real3 = Ksi*real2
    return real3

def deut_real_Xi_rev(freq,omegD,omegQ,A,eta,r,scale,phase):
    absor = deut_fit_rev(freq, omegD, omegQ, A, eta, r)
    disper = deut_disp_fit_rev(freq, omegD, omegQ, A, eta, r)
    real = absor*np.cos(phase) + disper*np.sin(phase)
    real2 = scale*real
    Ksi = Xi(r)
    real3 = Ksi*real2
    return real3

def deut_real_cube_Xi(freq,omegD,omegQ,A,eta,r,scale,phase, x3, x2, x1, x0):
    recur = deut_real_Xi(freq,omegD,omegQ,A,eta,r,scale,phase)
    cube = cubic(freq, x3, x2, x1, x0)
    return recur + cube

def deut_real_double_Xi(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase):
    Ksi = Xi(r)
    out = Ksi*deut_real_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase)
    return out

def deut_real_double_Xi_rev(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase):
    Ksi = Xi(r)
    out = Ksi*deut_real_double_rev(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase)
    return out

def deut_real_double_Xi_NonBolt(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, rN, rP, K, scale, phase):
    absor = deut_double_NonBolt(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, rN, rP, K, scale)
    disper = deut_disp_double_NonBolt(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, rN, rP, K, scale)
    real = absor*np.cos(phase) + disper*np.sin(phase)
    Ksi = 1#Xi(r)
    out = Ksi*real
    return out

def deut_real_double_HB(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase, f0, w, Ka, Kb):
    absor = deut_double_HB(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, f0, w, Ka, Kb)
    disper = deut_disp_double_HB(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, f0, w, Ka, Kb)
    real = absor*np.cos(phase) - disper*np.sin(phase)
    return real

def deut_real_double_cube(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase, x3, x2, x1, x0):
    recur = deut_real_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase)
    cube = cubic(freq, x3, x2, x1, x0)
    return recur + cube

def deut_real_double_cube_Xi(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase, x3, x2, x1, x0):
    recur = deut_real_double_Xi(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase)
    cube = cubic(freq, x3, x2, x1, x0)
    return recur + cube

def deut_real_double_cube_Xi_NonBolt(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, rN, rP, K, scale, phase, x3, x2, x1, x0):
    recur = deut_real_double_Xi_NonBolt(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, rN, rP, K, scale, phase)
    cube = cubic(freq, x3, x2, x1, x0)
    return recur + cube
    

def deut_real_double_cube_HB(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase, f0, w, Ka, Kb, x3, x2, x1, x0):
    recur = deut_real_double_HB(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase, f0, w, Ka, Kb)
    cube = cubic(freq, x3, x2, x1, x0)
    return recur + cube

def deut_real_double_lin(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase, x1, x0):
    recur = deut_real_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase)
    lin = freq*x1 + x0
    return recur + lin

def deut_real_double_lorz(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase, f0, gamma, cc, b):
    recur = deut_real_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase)
    lorz = lorentz2(freq, f0, gamma, cc, b)
    return recur + lorz

def deut_im_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase, offs, slope):
    absor = deut_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale)
    disper = deut_disp_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale)
    imag = absor*np.sin(phase) + disper*np.cos(phase) + offs + slope*freq
    return imag

def deut_false_asymm(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, ksi):
    R = (freq - omegD)/(3*omegQ1)
    absor = deut_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale)
    out = absor*(1 + 0.5*ksi*(1 + R))
    return out

def deut_false_asymm_cube(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, ksi, x3, x2, x1, x0):
    recur = deut_false_asymm(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, ksi)
    cube = cubic(freq, x3, x2, x1, x0)
    return recur + cube

def deut_false_asymm_zero(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, ksi):
    R = (freq - omegD)/(3*omegQ1)
    absor = deut_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale)
    out = absor*(1 + 0.5*ksi*(R))
    return out

def DIDhardCode(freq, r, slope, offs):
    omegD = 3.27705415e+01
    omegQ1 = 2.16712844e-02
    omegQ2 = 2.75852632e-02
    A1 = 2.90224589e-02
    eta1 = 1.01142710e-01
    eta2 = 1.37951403e-01
    K = 1.55024437e-01
    scale = 4.84541259e-04
    phase = -3.31813519e-01
    out = deut_im_double(freq,omegD,omegQ1,omegQ2,A1,eta1,eta2,r,K,scale,phase,offs,slope)
    return out

def DRD_fitR(freq, r):
    omegD = 3.27702256e+01
    omegQ1 = 2.16986220e-02
    omegQ2 = 2.59889140e-02
    A1 = 2.97697232e-02
    eta1 = 6.18836853e-02
    eta2 = 1.98712673e-01
    K = 1.94099997e-01
    scale = 5.12571386e-04
    phase = 1.90848493e-03
    out = deut_real_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase)
    return out

def DRD_fitR_cube(freq, phase, x0, x1, x2, x3):
    omegD=3.27699200e+01
    omegQ1=2.18377432e-02
    omegQ2=2.49030980e-02
    A1=1.94853669e-02
    eta1=6.10387396e-02
    eta2=2.43948117e-01
    r=1.34995364e+00
    K=2.97787454e-01
    scale=5.81930565e-04
    recur = deut_real_double_cube(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase, x0, x1, x2, x3)
    return recur

def ratio_method(r):
    pol = (r*r - 1)/(r + r*r + 1)
    return pol

def ratio_method_HO(r,omegD,omegQ): #higher order
    num = r*r - 1
    den = r*r + r*(1 + (6.0/5.0)*((np.log(r)*omegQ/omegD)**2)) + 1
    pol = num/den
    return pol

def ratio_tensor(r):
    pol = (r*r - 2*r + 1)/(r + r*r + 1)
    return pol

def ratioPderiv(r):
    deriv = (r**2 + 4*r + 1)/((r**2 + r + 1)**2)
    return deriv

def ratioQderiv(r):
    deriv = (3*(r**2 - 1))/((r**2 + r + 1)**2)
    return deriv

def riemann_step(ydata, step):
    area = 0.0
    Range = len(ydata)
    for i in range(Range):
        area += step*ydata[i]
    return area

def riemann_sum(xdata, ydata):
    area = 0.0
    Range = len(xdata)
    minim = xdata[0]
    maxim = xdata[Range-1]
    step = (maxim - minim)/Range
    for i in range(Range):
        area += step*ydata[i]
    return area

def chisq(data, fit):
    chisq =  0.0
    Range = len(data)
    for i in range(Range):
        chisq += abs(((data[i] - fit[i])**2)/fit[i])
    return chisq

def chisq_red(data, fit, std):
    chisq =  0.0
    Range = len(data)
    for i in range(Range):
        chisq += ((data[i] - fit[i])**2)/std**2
    red = chisq/(Range - 1)
    return red

def std(data, fit):
    summ = 0.0
    N = len(data)
    for i in range(N):
        summ += (data[i] - fit[i])**2
    out = np.sqrt((1/(N-1))*summ)
    return out

def freq2R(freq):
    R = 16.32516*freq - 537.58733
    return R

def TE_Pz_Proton(B, T):
    Pz = np.tanh(1.0217e-3*B/T)
    return Pz

def TE_Pz_Deut(B, T):
    g = 0.857438
    mu = 5.05e-27
    kB = 1.381e-23
    gmu2k = (g*mu)/(2*kB)
    num = 4*np.tanh(gmu2k*(B/T))
    den = 3 + np.tanh(gmu2k*(B/T))**2
    Pz = num/den
    return Pz
