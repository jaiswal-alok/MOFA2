from __future__ import division
import numpy.ma as ma
import numpy as np
import scipy as s
import math

from biofam.core.utils import dotd
from biofam.core import gpu_utils

# Import manually defined functions
from .variational_nodes import Constant_Variational_Node

class Y_Node(Constant_Variational_Node):
    def __init__(self, dim, value, noise_on='features'):
        self.noise_on = noise_on
        Constant_Variational_Node.__init__(self, dim, value)

        # Create a boolean mask of the data to hide missing values
        if type(self.value) != ma.MaskedArray:
            self.mask()
        ma.set_fill_value(self.value, 0.)

        self.mini_batch = None

    def precompute(self, options=None):
        # Precompute some terms to speed up the calculations
        self.N = self.dim[0] - ma.getmask(self.value).sum(axis=0)
        self.D = self.dim[1] - ma.getmask(self.value).sum(axis=1)

        gpu_utils.gpu_mode = options['gpu_mode']

        # Precompute the constant depending on the noise dimensions
        # TODO rewrite with no tau_d argument but problem is thatprecompute is
        # called before the markov_blanket is defined so we need this info here
        if self.noise_on == 'features':
            self.likconst = -0.5 * s.sum(self.N) * s.log(2.*s.pi)
        else:
            self.likconst = -0.5 * s.sum(self.D) * s.log(2.*s.pi)

    def mask(self):
        # Mask the observations if they have missing values
        self.value = ma.masked_invalid(self.value)

    def getMask(self):
        return ma.getmask(self.value)

    def define_mini_batch(self, ix):
        # define a minibatch of data for all nodes to use
        self.mini_batch = self.value[ix,:]

    def get_mini_batch(self):
        if self.mini_batch is None:
            return self.getExpectation()
        return self.mini_batch

    # @profile
    def calculateELBO(self):
        # Calculate evidence lower bound
        # Collect expectations from nodes

        Y_tmp = self.getExpectation()
        mask = ma.getmask(Y_tmp)
        Y = Y_tmp.data
        Y[mask] = 0.

        # TODO problem is its slow ... because we expand it -> to optimise
        Tau = self.markov_blanket["Tau"].getExpectations()

        Wtmp = self.markov_blanket["W"].getExpectations()
        Ztmp = self.markov_blanket["Z"].getExpectations()

        W, WW = Wtmp["E"], Wtmp["E2"]
        Z, ZZ = Ztmp["E"], Ztmp["E2"]

        ZW =  gpu_utils.array(Z).dot(gpu_utils.array(W.T))
        ZW[mask] = 0.

        term1 = gpu_utils.square(gpu_utils.array(Y))

        term2 = gpu_utils.array(ZZ).dot(gpu_utils.array(WW.T))
        term2[mask] = 0

        term3 = - gpu_utils.dot(gpu_utils.square(gpu_utils.array(Z)),gpu_utils.square(gpu_utils.array(W)).T)
        term3[mask] = 0.
        term3 += gpu_utils.square(ZW)

        ZW *= gpu_utils.array(Y)  # WARNING ZW becomes ZWY
        term4 = 2.*ZW

        tmp = 0.5 * (term1 + term2 + term3 - term4)

        Tau["lnE"][mask] = 0
        lik = self.likconst + 0.5 * gpu_utils.sum(gpu_utils.array(Tau["lnE"])) -\
              gpu_utils.sum(gpu_utils.array(Tau["E"]) * tmp)

        return lik

    def sample(self, dist='P'):
        # Y does NOT call sample recursively but relies on previous calls
        Z_samp = self.markov_blanket['Z'].samp
        W_samp = self.markov_blanket['W'].samp

        Tau_samp = self.markov_blanket['Tau'].samp
        F = Z_samp.dot(W_samp.transpose())

        var = 1./Tau_samp

        if self.markov_blanket['Tau'].__class__.__name__ == "TauN_Node": #TauN
            self.samp = np.array([s.random.normal(F[i, :], math.sqrt(var[i])) for i in range(F.shape[0])])
        else: #TauD
            self.samp = np.array([s.random.normal(F[:, i],math.sqrt(var[i])) for i in range(F.shape[1])]).T

        self.value = self.samp

        return self.samp
